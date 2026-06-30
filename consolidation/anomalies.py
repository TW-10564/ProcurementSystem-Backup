"""
Anomaly / data-entry-error detection (survey's #1 feature, 4.5).

Runs over the consolidated data + a re-read of 生産予測, NAS-Excel only, no
forecasting. Every finding is EXPLAINABLE (rule + offending value), so staff can
fix it. Designed for HIGH PRECISION (few false positives) — we verified the
balance-recurrence rule is too noisy (broken by legitimate Monday SAP stock
adjustments), so that is advisory-only (large unexplained jumps).

Layers:
  L1 Validity   — impossible/invalid values (negative stock, bad numbers,
                  aggregate rows, absurd safety/days). HIGH precision.
  L4 Outliers   — a month's 生産予測 value wildly off the SKU's own median
                  (likely a digit typo, e.g. an extra zero). Advisory.

Output: `anomalies` table in consolidated.db + a console report.
Run:  python3 anomalies.py   (run reconcile.py / consolidate.py first)
"""
import sqlite3
import statistics
from datetime import date, datetime

import consolidate
import master_desktop
from config_all import FILES, EXCESS_DAYS

DB_PATH = consolidate.DB_PATH

# tuning
EXTREME_DAYS = 3650        # >10 years of stock = likely dead/error (advisory)
OUTLIER_RATIO = 20.0       # isolated spike must be > 20x the SKU median ...
OUTLIER_FLOOR = 100        # ... and at least this many units above median
AGG_MARKERS = ("合計", "小計", "計（", "総計")


def _key(f):
    """Stable identity of a finding across runs: category|sku|type|field."""
    return f"{f[0]}|{f[2]}|{f[5]}|{f[4]}"


def init_table(conn):
    conn.executescript("""
    DROP TABLE IF EXISTS anomalies;                 -- current snapshot (rebuilt each run)
    CREATE TABLE anomalies (
        id INTEGER PRIMARY KEY,
        category TEXT, source_file TEXT, sku TEXT,
        sheet TEXT, field TEXT, atype TEXT, severity TEXT,
        value TEXT, expected TEXT, message TEXT,
        is_new INTEGER, first_seen TEXT
    );
    -- persistent history (NOT dropped):
    CREATE TABLE IF NOT EXISTS anomaly_state (
        key TEXT PRIMARY KEY, category TEXT, sku TEXT, atype TEXT, field TEXT,
        severity TEXT, value TEXT, message TEXT,
        first_seen TEXT, last_seen TEXT, status TEXT, fixed_ts TEXT
    );
    CREATE TABLE IF NOT EXISTS anomaly_runs (
        run_ts TEXT, total INTEGER, high INTEGER, med INTEGER, low INTEGER,
        new_count INTEGER, fixed_count INTEGER
    );
    """)
    conn.commit()


# ── Layer 1: validity rules (from consolidated_skus) ──────────────────────────

def layer1_validity(conn):
    rows = conn.execute("""
        SELECT category, source_file, sku, safety_days, stock_balance, stock_days,
               flag_negative FROM consolidated_skus""").fetchall()
    out = []
    for cat, src, sku, safety, bal, days, neg in rows:
        # impossible: negative stock
        if neg or (bal is not None and bal < 0):
            out.append((cat, src, sku, "部品詳細", "在庫残高", "negative_stock", "High",
                        f"{bal}", ">= 0", "在庫残高がマイナス（入力漏れ/誤り）"))
        # impossible: negative days/safety
        if days is not None and days < 0:
            out.append((cat, src, sku, "部品詳細", "在庫日数", "negative_days", "High",
                        f"{days}", ">= 0", "在庫日数がマイナス"))
        if safety is not None and safety < 0:
            out.append((cat, src, sku, "部品詳細", "安全在庫", "negative_safety", "High",
                        f"{safety}", ">= 0", "安全在庫日数がマイナス"))
        # aggregate row mixed into detail (not an orderable SKU)
        if any(m in sku for m in AGG_MARKERS):
            out.append((cat, src, sku, "部品詳細", "綽名", "aggregate_row", "Med",
                        sku, "個別SKU", "合計/小計行が明細に混在（個別部品ではない）"))
        # advisory: extreme stock days
        if days is not None and days > EXTREME_DAYS:
            out.append((cat, src, sku, "部品詳細", "在庫日数", "extreme_days", "Low",
                        f"{days:.0f}", f"<= {EXTREME_DAYS}",
                        "在庫日数が極端に大（滞留/誤りの可能性）"))
    return out


# ── Layer 4: statistical outliers on monthly 生産予測 (digit-typo catcher) ─────

def layer4_outliers():
    """Flag ONLY isolated spikes (likely digit typos): a value far above the
    SKU's median AND far above both neighbours. Real campaigns sustain over
    several months, so they are not flagged."""
    out = []
    for cat in FILES:
        try:
            series = master_desktop.read_seisan(cat)
        except Exception:
            continue
        for sku, months in series.items():
            items = sorted((ym, v) for ym, v in months.items()
                           if isinstance(v, (int, float)) and v >= 0)
            if len(items) < 6:
                continue
            med = statistics.median(v for _, v in items)
            if med <= 0:
                continue
            for idx, (ym, v) in enumerate(items):
                if v <= med * OUTLIER_RATIO or (v - med) <= OUTLIER_FLOOR:
                    continue
                prev = items[idx - 1][1] if idx > 0 else None
                nxt = items[idx + 1][1] if idx < len(items) - 1 else None
                neigh = [x for x in (prev, nxt) if x is not None]
                # isolated: both neighbours present and each < 1/10 of the spike
                if neigh and all(x < v / 10 for x in neigh):
                    out.append((cat, FILES[cat]["file"], sku, "生産予測",
                                f"{ym[0]}-{ym[1]:02d}", "value_outlier", "Low",
                                f"{v:.0f}", f"中央値≈{med:.0f}",
                                f"前後より突出・中央値の{v/med:.0f}倍（桁ミス等の可能性）"))
    return out


SEL = """SELECT category, source_file, sku, sheet, field, atype, severity,
                value, expected, message, is_new FROM anomalies"""


def build_portal_data():
    """Anomaly findings for the portal tab — reads the precomputed `anomalies`
    table (instant) plus the run history. If empty, runs detection once."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(SEL).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        conn.close()
        run()
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(SEL).fetchall()

    try:
        runs = conn.execute(
            """SELECT run_ts, total, high, med, low, new_count, fixed_count
               FROM anomaly_runs ORDER BY rowid DESC LIMIT 8""").fetchall()
    except sqlite3.OperationalError:
        runs = []
    conn.close()

    sev = {"High": 0, "Med": 0, "Low": 0}
    types = {}
    items = []
    for r in rows:
        sev[r[6]] = sev.get(r[6], 0) + 1
        types[r[5]] = types.get(r[5], 0) + 1
        items.append({"cat": r[0], "file": r[1], "sku": r[2], "sheet": r[3],
                      "field": r[4], "type": r[5], "severity": r[6],
                      "value": r[7], "expected": r[8], "message": r[9],
                      "is_new": bool(r[10])})
    order = {"High": 0, "Med": 1, "Low": 2}
    # new items first within each severity
    items.sort(key=lambda x: (order.get(x["severity"], 9), not x["is_new"]))

    latest = runs[0] if runs else None
    history = [{"ts": r[0], "total": r[1], "new": r[5], "fixed": r[6]} for r in runs]
    return {"total": len(items), "by_severity": sev, "by_type": types,
            "findings": items,
            "new_count": latest[5] if latest else 0,
            "fixed_count": latest[6] if latest else 0,
            "last_run": latest[0] if latest else None,
            "history": history}


def run():
    conn = sqlite3.connect(DB_PATH)
    # ensure consolidated data exists
    n = conn.execute("SELECT COUNT(*) FROM consolidated_skus").fetchone()
    if not n or n[0] == 0:
        conn.close()
        import reconcile
        reconcile.run()
        conn = sqlite3.connect(DB_PATH)

    init_table(conn)
    findings = layer1_validity(conn) + layer4_outliers()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── diff vs previous run (history tracking) ──
    cur = {_key(f): f for f in findings}
    prev_open, first_seen_map = set(), {}
    for k, st, fs in conn.execute("SELECT key, status, first_seen FROM anomaly_state"):
        first_seen_map[k] = fs
        if st == "open":
            prev_open.add(k)
    new_keys = set(cur) - prev_open       # appearing now (incl. reopened)
    fixed_keys = prev_open - set(cur)     # were open, now resolved

    # update persistent state
    for k, f in cur.items():
        is_new = k not in prev_open
        fs = now if is_new else first_seen_map.get(k, now)
        conn.execute(
            """INSERT OR REPLACE INTO anomaly_state
               (key,category,sku,atype,field,severity,value,message,first_seen,last_seen,status,fixed_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'open', NULL)""",
            (k, f[0], f[2], f[5], f[4], f[6], f[7], f[9], fs, now))
    for k in fixed_keys:
        conn.execute("UPDATE anomaly_state SET status='fixed', fixed_ts=? WHERE key=?", (now, k))

    # write current snapshot (with is_new / first_seen)
    for f in findings:
        k = _key(f)
        conn.execute(
            """INSERT INTO anomalies
               (category,source_file,sku,sheet,field,atype,severity,value,expected,message,is_new,first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*f, 1 if k in new_keys else 0, now if k in new_keys else first_seen_map.get(k, now)))

    by_sev = {"High": 0, "Med": 0, "Low": 0}
    by_type = {}
    for f in findings:
        by_sev[f[6]] = by_sev.get(f[6], 0) + 1
        by_type[f[5]] = by_type.get(f[5], 0) + 1
    conn.execute(
        """INSERT INTO anomaly_runs (run_ts,total,high,med,low,new_count,fixed_count)
           VALUES (?,?,?,?,?,?,?)""",
        (now, len(findings), by_sev["High"], by_sev["Med"], by_sev["Low"],
         len(new_keys), len(fixed_keys)))
    conn.commit()

    # report
    print(f"=== Anomaly detection — {now} ===\n")
    print(f"  TOTAL findings: {len(findings)}  "
          f"(🔴 High {by_sev['High']} · 🟡 Med {by_sev['Med']} · ⚪ Low {by_sev['Low']})")
    print(f"  vs last run:  🆕 new {len(new_keys)}  ·  ✅ fixed {len(fixed_keys)}")
    print("  by type:", ", ".join(f"{k}={v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1])))
    conn.close()
    return findings


if __name__ == "__main__":
    run()
