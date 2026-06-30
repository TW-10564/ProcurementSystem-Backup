"""
実在庫 paste-ready builder — the openpyxl-SAFE write path for 在庫残高自動更新.

WHY THIS EXISTS
  在庫残高 in 部品詳細 is an Excel FORMULA (前日残 − 生産計画 + 入庫), and writing
  into the ONE files with openpyxl STRIPS every formula's cached value (proven:
  a no-op load+save turns a real balance into None). So we never rewrite the ONE
  files. Instead we emit a NEW workbook — a 綽名 → 実在庫 lookup block the user
  pastes into a dedicated sheet, which the ONE file's seed cell then references
  (VLOOKUP/XLOOKUP). openpyxl creating a fresh file is 100% safe.

PRIMARY SOURCE — 260629在庫.xlsx (Access-processed weekly output)
  Columns: SBRCODE | SBRNAME | あだ名 | SYOCODE | SYONAME | QTYの合計
  あだ名 IS the join key to ONE 綽名 (many SYOCODE rows are summed per あだ名).
  This file is generated Monday mornings from the ZM13 SAP extract via Access,
  and already contains the correct aggregation (e.g. LD5-16G56 = Samsung +
  Hynix + Micron + 汎用コード 179506, summed). Use it as-is; we just join.

  Override path:  SAKO_FILE=/path/to/YYMMDD在庫.xlsx  python3 stock_paste.py
  File discovery: auto-searches parent dir for the most recent *在庫*.xlsx

WHAT IT PRODUCES
  実在庫_貼付用_YYYYMMDD.xlsx:
    • 実在庫マスター  — 大分類 | 綽名 | 実在庫 | あだ名 | SAP品目数 | ONE在庫残高(現)
                       amber fill on 実在庫 column, red fill on unmatched rows
    • カバレッジ      — per-category match rate + unmatched 綽名 list + analysis notes
"""
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

import consolidate

HERE = Path(__file__).parent
DB_PATH = consolidate.DB_PATH

# ── source file resolution ────────────────────────────────────────────────────

def _find_sako_file():
    """Find the most recent *在庫*.xlsx in the project root (parent of consolidation/)."""
    project_root = HERE.parent
    candidates = sorted(project_root.glob("*在庫*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None

SAKO_FILE = Path(os.environ["SAKO_FILE"]) if os.environ.get("SAKO_FILE") else _find_sako_file()
SAKO_SHEET = "Sheet1"   # Access export sheet name

# ── name normalisation (hyphen/space/width-insensitive) ───────────────────────

def norm(x):
    if x is None:
        return ""
    s = str(x)
    # fullwidth → halfwidth (covers Ａ-Ｚ, ０-９, common symbols like Ｂ450Ｗ in SAP)
    s = s.translate(str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
        "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
        "０１２３４５６７８９",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
    ))
    s = s.upper()
    for ch in "　 -_+()／/":
        s = s.replace(ch, "")
    return s.strip()


# suffix tokens that appear in ONE 綽名 but not in SAP あだ名 (retail-box labels etc.)
_STRIP_SUFFIXES = (" MPK",)

def norm_sku(sku):
    """Normalize a ONE 綽名 for matching: apply norm() then strip known ONE-only suffixes."""
    for sfx in _STRIP_SUFFIXES:
        if sku.upper().endswith(sfx.upper()):
            sku = sku[: -len(sfx)].strip()
    return norm(sku)


# ── SYONAME-based fallback matcher (for blank-あだ名 rows) ────────────────────
# Handles MB (all 31 rows have blank あだ名), WD NVMe SSDs, FAN accessories, etc.

_BRAND_RE  = re.compile(
    r'^(?:ASRock|ASUS|MSI|GIGABYTE|Scythe|NOCTUA|Seasonic|SilverStone|'
    r'Kingston|BUFFALO|AINEX|SLR|IRC|FLU|FKI|ASETEK|ENH|W\.D\.|W\.D|LIT)\s+', re.I)
_SERIES_RE = re.compile(r'^(?:PRIME|ProArt|PG|PRO|MAG)\s+', re.I)

def _clean_syoname(s):
    """Extract a matchable model key from a SAP SYONAME string."""
    if not s:
        return ""
    s = str(s)
    s = re.sub(r'^組\s*', '', s)
    s = _BRAND_RE.sub('', s)
    s = _SERIES_RE.sub('', s)
    s = re.sub(r'\s*法.*', '', s)
    s = re.sub(r'\s*\(.*$', '', s)        # strip from first ( — handles truncated cells
    s = re.sub(r'\bTW\s+(BK|WH)\b', r'\1', s)
    s = re.sub(r'\bTW\b', '', s)
    for noise in (' WiFi', ' WIFI', '-CREATOR', '-SAGE SE', '-NOVA',
                  '-CSM-SI', '-AJ-SI', '-AJ ', ' EVO', ' WS', '-W '):
        s = s.replace(noise, ' ')
    s = re.sub(r'\s+Pro4\b', '', s, flags=re.I)
    s = re.sub(r'-[NEADB]{1,3}$', '', s)
    s = re.sub(r'-[0-9]$', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _decode_wd_nvme(syoname):
    """WD NVMe: SN5100 WDS200T → SN51002T (matches ONE naming convention)."""
    m = re.search(r'SN(\d{4})\s+WDS(\d+)', str(syoname), re.I)
    if not m:
        return None
    model2 = str(int(m.group(1)) // 100)
    cap3   = f"{int(m.group(2)) // 100:03d}"
    return f"SN{model2}{cap3}T"


def build_syoname_idx(rows):
    """Build {norm_key: slot} from blank-あだ名 rows using SYONAME heuristics."""
    idx = {}

    def _add(key, qty, syoname, sbrname):
        n = norm(key)
        if not n:
            return
        if n not in idx:
            idx[n] = {"qty": 0, "syoname": str(syoname)[:60], "src": str(key)[:40],
                      "sbrname": sbrname, "codes": 0}
        idx[n]["qty"] += qty
        idx[n]["codes"] += 1

    for r in rows[1:]:
        sbrname, adana, syoname, qty = r[1], r[2], r[4], r[5]
        if adana:
            continue
        if not syoname:
            continue
        qty = qty or 0
        wd = _decode_wd_nvme(syoname)
        if wd:
            _add(wd, qty, syoname, sbrname)
            continue
        cleaned = _clean_syoname(syoname)
        _add(cleaned, qty, syoname, sbrname)
        m = re.search(r'\(([^)]{2,20})\)', str(syoname))
        if m:
            _add(m.group(1).strip(), qty, syoname, sbrname)

    return idx


# ── 260629在庫 reader ─────────────────────────────────────────────────────────

def read_sako_stock(path=None):
    """Return ({norm(あだ名): slot}, raw_rows).
    Slot keys: qty, src, codes. Multiple SYOCODE rows per あだ名 are summed."""
    if path is None:
        path = SAKO_FILE
    if path is None or not Path(path).exists():
        raise FileNotFoundError(
            f"在庫ファイルが見つかりません。SAKO_FILE 環境変数で指定するか、"
            f"プロジェクトルートに *在庫*.xlsx を置いてください。 (looked in {HERE.parent})"
        )
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb[SAKO_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = [str(h).strip() if h else "" for h in rows[0]]
    try:
        col_adana = next(i for i, h in enumerate(header) if "あだ名" in h or "adana" in h.lower())
        col_qty   = next(i for i, h in enumerate(header) if "QTY" in h.upper() or "合計" in h)
    except StopIteration:
        raise RuntimeError(f"Expected columns not found in {Path(path).name}. Header: {header}")

    idx = {}
    for r in rows[1:]:
        adana = r[col_adana] if col_adana < len(r) else None
        if not adana:
            continue
        n = norm(adana)
        if not n:
            continue
        qty = r[col_qty] if col_qty < len(r) else None
        if not isinstance(qty, (int, float)):
            qty = 0
        if n not in idx:
            idx[n] = {"qty": 0, "src": str(adana).strip(), "codes": 0}
        idx[n]["qty"] += qty
        idx[n]["codes"] += 1
    return idx, rows


# ── DB read ───────────────────────────────────────────────────────────────────

def load_consolidated():
    """Return the 534 SKUs in parse order (category, 綽名, ONE在庫残高)."""
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM consolidated_skus").fetchone()[0]
    if n == 0:
        conn.close()
        consolidate.run()
        conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, category, sku, stock_balance FROM consolidated_skus ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


# ── matching ──────────────────────────────────────────────────────────────────

def match(sku, sako_idx, syoname_idx):
    """Return (slot, method_label) or (None, None).
    Priority: norm → norm_sku (suffix-stripped) → SYONAME heuristic."""
    n = norm(sku)
    if n and n in sako_idx:
        return sako_idx[n], "正規化"
    ns = norm_sku(sku)
    if ns and ns != n and ns in sako_idx:
        return sako_idx[ns], "接尾辞除去"
    if n and n in syoname_idx:
        return syoname_idx[n], "SYONAME"
    if ns and ns != n and ns in syoname_idx:
        return syoname_idx[ns], "SYONAME"
    return None, None


def build_rows():
    sako_idx, raw_rows = read_sako_stock()
    raw_idx     = {slot["src"]: slot for slot in sako_idx.values()}
    syoname_idx = build_syoname_idx(raw_rows)
    skus = load_consolidated()
    out = []
    for _id, cat, sku, bal in skus:
        slot = raw_idx.get(sku) or raw_idx.get(sku.strip())
        method = "直接" if slot else None
        if not slot:
            slot, method = match(sku, sako_idx, syoname_idx)
        out.append({
            "category": cat, "sku": sku, "one_balance": bal,
            "matched": slot is not None, "method": method or "",
            "adana": slot["src"] if slot else "",
            "qty": slot["qty"] if slot else None,
            "codes": slot["codes"] if slot else 0,
        })
    return out, Path(SAKO_FILE).name if SAKO_FILE else "不明"


# ── Excel output ──────────────────────────────────────────────────────────────

HDR_FILL  = PatternFill("solid", fgColor="1F4E78")
HDR_FONT  = Font(color="FFFFFF", bold=True, size=10)
PASTE_FILL = PatternFill("solid", fgColor="FFF2CC")  # amber = paste column
MISS_FILL  = PatternFill("solid", fgColor="FCE4D6")  # light red = unmatched
THIN   = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# categories where unmatched is structurally expected (not an error)
STRUCTURAL_NOMATCH = {"NotePC", "Case_梱包"}


def write_excel(rows, src_name, out_path):
    wb = openpyxl.Workbook()

    # ── Sheet 1: 実在庫マスター ──
    ws = wb.active
    ws.title = "実在庫マスター"
    ws["A1"] = "実在庫 貼付用マスター（綽名 → SAP実在庫）"
    ws["A1"].font = Font(bold=True, size=13, color="1F4E78")
    ws["A2"] = (f"元データ: {src_name}  ｜  "
                f"黄=実在庫(SAP QTYの合計, 貼付/VLOOKUP用)  ｜  "
                f"赤=SAP未対応  ｜  ※NotePC・梱包材はSAP部品外のため未対応は正常")
    ws["A2"].font = Font(italic=True, size=9, color="808080")

    headers = ["大分類", "綽名", "実在庫(SAP)", "あだ名", "SAP品目数", "一致方法", "ONE在庫残高(現)"]
    hrow = 4
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=hrow, column=j, value=h)
        c.fill = HDR_FILL; c.font = HDR_FONT
        c.alignment = Alignment(horizontal="center"); c.border = BORDER

    r = hrow + 1
    for row in rows:
        is_structural = row["category"] in STRUCTURAL_NOMATCH
        vals = [row["category"], row["sku"], row["qty"],
                row["adana"], row["codes"] or "", row["method"], row["one_balance"]]
        for j, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=j, value=v)
            cell.border = BORDER
        ws.cell(row=r, column=3).fill = PASTE_FILL            # 実在庫 = amber paste col
        if not row["matched"] and not is_structural:
            for j in range(1, len(headers) + 1):
                ws.cell(row=r, column=j).fill = MISS_FILL     # actionable miss = red
        r += 1

    ws.freeze_panes = "C5"
    for col, w in zip("ABCDEFG", [12, 30, 14, 22, 11, 10, 16]):
        ws.column_dimensions[col].width = w

    # ── Sheet 2: カバレッジ ──
    ws2 = wb.create_sheet("カバレッジ")
    ws2["A1"] = "カバレッジ — SAP実在庫の対応状況"
    ws2["A1"].font = Font(bold=True, size=13, color="1F4E78")
    ws2["A2"] = "★ = 構造的に未対応が正常な分類（NotePC=完成品、Case_梱包=包材）"
    ws2["A2"].font = Font(italic=True, size=9, color="808080")

    per = defaultdict(lambda: {"total": 0, "matched": 0, "structural": False})
    for row in rows:
        per[row["category"]]["total"] += 1
        per[row["category"]]["matched"] += row["matched"]
        if row["category"] in STRUCTURAL_NOMATCH:
            per[row["category"]]["structural"] = True

    head = ["大分類", "綽名数", "対応", "未対応", "対応率", "備考"]
    for j, h in enumerate(head, 1):
        c = ws2.cell(row=4, column=j, value=h)
        c.fill = HDR_FILL; c.font = HDR_FONT; c.border = BORDER

    rr = 5
    tot = matched_total = 0
    actionable_total = actionable_matched = 0
    for cat in sorted(per):
        s = per[cat]
        miss = s["total"] - s["matched"]
        rate = 100 * s["matched"] / s["total"] if s["total"] else 0
        note = "★ 完成品/包材" if s["structural"] else ""
        tot += s["total"]; matched_total += s["matched"]
        if not s["structural"]:
            actionable_total += s["total"]
            actionable_matched += s["matched"]
        for j, v in enumerate([cat, s["total"], s["matched"], miss, f"{rate:.0f}%", note], 1):
            ws2.cell(row=rr, column=j, value=v).border = BORDER
        if s["structural"]:
            ws2.cell(row=rr, column=5).fill = PatternFill("solid", fgColor="EEEEEE")
        elif rate < 50:
            ws2.cell(row=rr, column=5).fill = MISS_FILL
        else:
            ws2.cell(row=rr, column=5).fill = PASTE_FILL
        rr += 1

    overall = 100 * matched_total / tot if tot else 0
    act_rate = 100 * actionable_matched / actionable_total if actionable_total else 0
    ws2.cell(row=rr + 1, column=1, value="全体合計").font = Font(bold=True)
    ws2.cell(row=rr + 1, column=2, value=tot).font = Font(bold=True)
    ws2.cell(row=rr + 1, column=3, value=matched_total).font = Font(bold=True)
    ws2.cell(row=rr + 1, column=4, value=tot - matched_total).font = Font(bold=True)
    ws2.cell(row=rr + 1, column=5, value=f"{overall:.0f}%").font = Font(bold=True, color="375623")
    ws2.cell(row=rr + 2, column=1, value="部品分類のみ").font = Font(italic=True, color="595959")
    ws2.cell(row=rr + 2, column=5, value=f"{act_rate:.0f}%").font = Font(bold=True, italic=True, color="375623")
    ws2.cell(row=rr + 2, column=6, value="(NotePC・梱包材を除いた実質対応率)").font = Font(italic=True, size=9, color="595959")

    # unmatched list (actionable only)
    ws2.cell(row=rr + 4, column=1, value="未対応の綽名（要確認 — 包材・完成品は除外済）").font = Font(bold=True, color="C00000")
    for j, h in enumerate(["大分類", "綽名", "ONE在庫残高(現)"], 1):
        c = ws2.cell(row=rr + 5, column=j, value=h)
        c.fill = HDR_FILL; c.font = HDR_FONT; c.border = BORDER
    ur = rr + 6
    for row in rows:
        if not row["matched"] and row["category"] not in STRUCTURAL_NOMATCH:
            ws2.cell(row=ur, column=1, value=row["category"]).border = BORDER
            ws2.cell(row=ur, column=2, value=row["sku"]).border = BORDER
            ws2.cell(row=ur, column=3, value=row["one_balance"]).border = BORDER
            ur += 1

    for col, w in zip("ABCDEF", [12, 22, 10, 10, 10, 28]):
        ws2.column_dimensions[col].width = w

    wb.save(out_path)
    return overall, act_rate


def build_portal_data():
    rows, src_name = build_rows()
    matched = sum(1 for r in rows if r["matched"])
    total = len(rows)
    act_rows = [r for r in rows if r["category"] not in STRUCTURAL_NOMATCH]
    act_matched = sum(1 for r in act_rows if r["matched"])
    act_total = len(act_rows)

    per = defaultdict(lambda: {"total": 0, "matched": 0, "structural": False})
    for r in rows:
        per[r["category"]]["total"] += 1
        per[r["category"]]["matched"] += 1 if r["matched"] else 0
        if r["category"] in STRUCTURAL_NOMATCH:
            per[r["category"]]["structural"] = True

    return {
        "source": src_name,
        "matched": matched,
        "total": total,
        "coverage": round(100 * matched / total) if total else 0,
        "parts_coverage": round(100 * act_matched / act_total) if act_total else 0,
        "by_cat": [
            {"cat": cat, "matched": v["matched"], "total": v["total"],
             "rate": round(100 * v["matched"] / v["total"]) if v["total"] else 0,
             "structural": v["structural"]}
            for cat, v in sorted(per.items())
        ],
        "rows": [
            {"cat": r["category"], "sku": r["sku"], "bal": r["one_balance"],
             "matched": r["matched"], "method": r["method"],
             "adana": r["adana"], "qty": r["qty"], "codes": r["codes"]}
            for r in rows
        ],
    }


def run():
    rows, src_name = build_rows()
    out = HERE / f"実在庫_貼付用_{date.today():%Y%m%d}.xlsx"
    overall, act_rate = write_excel(rows, src_name, out)

    matched = sum(1 for r in rows if r["matched"])
    print(f"=== 実在庫 paste-ready builder — {date.today()} ===")
    print(f"  source      : {src_name}")
    print(f"  matched     : {matched}/{len(rows)} = {overall:.0f}%  (部品分類のみ: {act_rate:.0f}%)")
    miss_act = [(r["category"], r["sku"]) for r in rows
                if not r["matched"] and r["category"] not in STRUCTURAL_NOMATCH]
    from collections import Counter
    print(f"  要確認 by cat : {Counter(c for c, _ in miss_act).most_common()}")
    print(f"  → {out.name}")
    return out


if __name__ == "__main__":
    run()
