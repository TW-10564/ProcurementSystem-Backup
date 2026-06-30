# ONE Consolidation Portal — Claude Context

## What this is
AI procurement decision-support system for PC parts buying at サードウェーブ.
**Phase 1** (current): Excel automation — reads 17 ONE system Excel files from NAS,
consolidates inventory data, and serves a FastAPI dashboard.

No forecasting. No SAP write-back. NAS-only. All logic is read-only against the ONE files.

---

## How to run

```bash
cd consolidation
python3 -m uvicorn app:app --host 0.0.0.0 --port 8501
# → http://localhost:8501
```

Refresh button on the portal re-runs consolidation + anomaly + purchasing + stock-paste.

---

## Architecture

```
consolidation/
  config_all.py       ← per-file column layout for all 17 ONE files (the "spec")
  consolidate.py      ← parses 部品詳細 sheets → SQLite (consolidated.db)
  reconcile.py        ← accuracy gate: parsed vs expected SKU counts, PASS/WARN/FAIL
  anomalies.py        ← data-entry anomaly detection (negative stock, outliers, etc.)
  purchasing.py       ← overdue POs, price/supplier history, incoming-excess warnings
  master_desktop.py   ← auto-generates 部品予測 master from 17 sub-files
  stock_paste.py      ← maps SAP 実在庫 (260629在庫.xlsx) → ONE 綽名, builds paste Excel
  excel_writer.py     ← exports formatted 統合出力 Excel
  app.py              ← FastAPI portal (routes: / /refresh /anomalies /procurement /master /stock)
  index.html          ← single-page dashboard (6 tabs, lazy-loaded, ja/en toggle)
  consolidated.db     ← SQLite, generated on first run (gitignored)
```

ONE source files live at: `../ONE/ONE/` (relative to consolidation/).
That path is set by `BASE` in `config_all.py`:
```python
_DEFAULT_BASE = Path(__file__).parent.parent / "ONE" / "ONE"
BASE = Path(os.environ.get("ONE_SOURCE_DIR", _DEFAULT_BASE))
```
Override with `ONE_SOURCE_DIR=/path/to/files` if files move.

---

## Key data concepts

**Join key**: `綽名` (あだ名) — the short product nickname used across all ONE files.
Every SKU in 部品詳細 has a 綽名; it is also the join key to the SAP stock file.

**ONE file layout** (部品詳細 sheet): 4-row repeating blocks —
```
row 0: 生産計画  | [綽名]   | [date cols →]
row 1: 購買計画  | [safety] |
row 2: 在庫残高  |          | [stock balance per date]
row 3: 在庫日数  |          | [stock days per date]
```
Column offsets differ per file — all captured in `config_all.py` as `label_col / name_col / first_date_col`.

**SAP stock file** (`260629在庫.xlsx` in project root):
Access-processed weekly ZM13 extract. Columns: `SBRCODE | SBRNAME | あだ名 | SYOCODE | SYONAME | QTYの合計`.
The `あだ名` column joins to ONE 綽名. Multiple SYOCODE rows per あだ名 are summed.
31 ﾏｻﾞｰﾎﾞｰﾄﾞ rows have blank あだ名 → matched via SYONAME heuristic in `stock_paste.py`.
Current coverage: **323/534 = 60% overall, 70% parts-only** (NotePC + 梱包 are structural misses).

---

## CRITICAL constraints

1. **Never rewrite ONE source files with openpyxl.** A no-op load+save strips all formula
   cached values (proven: stock balance goes `6695 → None`). Only ever create NEW workbooks.
   `stock_paste.py` exists specifically to avoid this — it emits a separate paste file.

2. **`consolidated.db` is always regenerated** — never treat it as source of truth across sessions.
   Run `python3 consolidate.py` or hit /refresh to rebuild.

3. **Snapshot date logic**: `consolidate.py` picks the first date column ≥ today. If the file
   is stale (no column reaches today), it falls back to the last column and sets `snapshot_stale=True`.

---

## 17 ONE files (config_all.py)

| Category    | File                          | Format | SKUs |
|-------------|-------------------------------|--------|------|
| CPU         | ONE_08_CPU_社外秘.xlsb        | xlsb   | 38   |
| MB          | ONE_08_MB_社外秘.xlsb         | xlsb   | 38   |
| VGA         | ONE_08_VGA_社外秘.xlsx        | xlsx   | 28   |
| RTX         | ONE_08_RTX_社外秘.xlsx        | xlsx   | 24   |
| MEM         | ONE_08_MEM_社外秘.xlsx        | xlsx   | 26   |
| SSD         | ONE_08_SSD_社外秘.xlsx        | xlsx   | 45   |
| HDD         | ONE_08_HDD_社外秘.xlsx        | xlsx   | 9    |
| PSU         | ONE_08_PSU_社外秘.xlsx        | xlsx   | 17   |
| Case        | ONE_08_Case_社外秘.xlsx       | xlsx   | 42   |
| Case_other  | ONE_08_Case_other_社外秘.xlsx | xlsx   | 19   |
| Case_梱包    | ONE_08_Case_梱包_社外秘.xlsx  | xlsx   | 60   |
| FAN         | ONE_08_FAN_社外秘.xlsb        | xlsb   | 77   |
| ODD         | ONE_08_ODD_社外秘.xlsb        | xlsb   | 7    |
| NotePC      | ONE_08_NotePC_社外秘.xlsx     | xlsx   | 81   |
| OS          | ONE_08_OS_社外秘.xlsx         | xlsx   | 10   |
| Mouse&KB    | ONE_08_Mouse&KB_社外秘.xlsx   | xlsx   | 6    |
| 国内一般品   | ONE_08_国内一般品_社外秘.xlsx  | xlsx   | 7    |

Total expected: **534 SKUs**. Reconciliation gate passes when parsed == expected for all files.

---

## Portal tabs

| Tab | Route | Load | Description |
|-----|-------|------|-------------|
| サマリー | embedded | eager | Category roll-up |
| 統合一覧 | embedded | eager | All 534 SKUs, searchable |
| 照合レポート | embedded | eager | Per-file PASS/WARN/FAIL |
| 異常検出 | /anomalies | lazy | Data-entry anomaly findings |
| 発注・価格 | /procurement | lazy | Overdue POs, price history, incoming-excess |
| マスター自動化 | /master | lazy | 部品予測 master auto-generation |
| 在庫自動設定 | /stock | lazy | SAP 実在庫 → ONE 綽名 match coverage |

---

## Dependencies

```
fastapi uvicorn openpyxl pyxlsb python-docx
```

Install: `pip install fastapi uvicorn openpyxl pyxlsb python-docx`

---

## What is NOT in this repo (gitignored, lives on NAS/OneDrive)

- `ONE/` — all 17 ONE Excel source files
- `260629在庫.xlsx` — weekly SAP stock export (replace with the latest each Monday)
- `SAP在庫(ZM13)0629.xlsx` — raw ZM13 extract before Access processing
- `販売計画*.xlsx` — sales forecast files
- `consolidated.db`, `__pycache__/`, generated output Excel files
