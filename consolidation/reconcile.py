"""
Reconciliation check for the consolidation.

Makes "is it accurate?" a number, not a hope. For every file it cross-checks
three independent signals and assigns a status:

  1. blocks_seen      — raw count of 生産計画 blocks found in 部品詳細 (structure)
  2. skus_parsed      — rows that produced a usable SKU record (what we kept)
  3. expected_skus    — external truth from notes Section 31 (the audit baseline)

  PASS  : skus_parsed == expected AND no blocks silently dropped
  WARN  : parsed != expected but data extracted (layout drift / count moved)
  FAIL  : sheet missing, 0 parsed, or read error

Also surfaces data-quality signals (negative balances) so the consolidation
flags bad source data rather than laundering it into a clean-looking master.

Run:  python3 reconcile.py
"""
import sqlite3
from datetime import date
from pathlib import Path
import consolidate
from config_all import FILES, TOTAL_EXPECTED_SKUS

DB_PATH = consolidate.DB_PATH


def classify(stats):
    if not stats["sheet_found"]:
        return "FAIL"
    if stats["skus_parsed"] == 0:
        return "FAIL"
    # blocks that had a SKU should all be parsed; skipped_no_name are legit empties
    dropped = stats["blocks_seen"] - stats["skus_parsed"] - stats["skipped_no_name"]
    if stats["skus_parsed"] == stats["expected_skus"] and dropped == 0:
        return "PASS"
    return "WARN"


def run(snapshot_date=None):
    if snapshot_date is None:
        snapshot_date = date.today()

    print(f"=== Consolidation reconciliation — snapshot {snapshot_date} ===\n")
    all_stats = consolidate.run(snapshot_date)

    conn = sqlite3.connect(DB_PATH)

    # independent data-quality cross-check straight from the written table
    dq = dict(conn.execute(
        "SELECT category, SUM(flag_negative) FROM consolidated_skus GROUP BY category"
    ).fetchall())
    total_rows = conn.execute("SELECT COUNT(*) FROM consolidated_skus").fetchone()[0]

    print("\n" + "=" * 78)
    print(f"{'category':<12}{'parsed':>7}{'expect':>7}{'blocks':>7}{'neg':>5}  status")
    print("-" * 78)

    rows_for_db, n_pass, n_warn, n_fail = [], 0, 0, 0
    for s in all_stats:
        status = classify(s)
        n_pass += status == "PASS"
        n_warn += status == "WARN"
        n_fail += status == "FAIL"
        neg = dq.get(s["category"], 0) or 0
        mark = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}[status]
        print(f"{s['category']:<12}{s['skus_parsed']:>7}{s['expected_skus']:>7}"
              f"{s['blocks_seen']:>7}{neg:>5}  {mark} {status}"
              f"{('  ' + s['note']) if s['note'] else ''}")
        rows_for_db.append((
            s["category"], s["file"], int(s["sheet_found"]),
            s["blocks_seen"], s["skus_parsed"], s["expected_skus"],
            s["skipped_no_name"], s["snapshot_date"], status, s["note"],
        ))

    conn.executemany(
        """INSERT INTO reconciliation
           (category, source_file, sheet_found, blocks_seen, skus_parsed,
            expected_skus, skipped_no_name, snapshot_date, status, note)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows_for_db,
    )
    conn.commit()

    total_parsed = sum(s["skus_parsed"] for s in all_stats)
    total_neg = sum((dq.get(s["category"], 0) or 0) for s in all_stats)
    pct = 100 * total_parsed / TOTAL_EXPECTED_SKUS if TOTAL_EXPECTED_SKUS else 0

    print("=" * 78)
    print(f"TOTAL parsed {total_parsed} / {TOTAL_EXPECTED_SKUS} expected "
          f"({pct:.1f}%)   files: {n_pass} PASS · {n_warn} WARN · {n_fail} FAIL")
    print(f"Data-quality flags: {total_neg} SKUs with negative stock balance "
          f"(carried into master as flag_negative, not silently dropped)")
    print(f"\nAccuracy gate: {'✓ PASS' if n_fail == 0 and total_parsed == TOTAL_EXPECTED_SKUS else '✗ REVIEW NEEDED'}")
    conn.close()
    return n_fail == 0 and total_parsed == TOTAL_EXPECTED_SKUS


if __name__ == "__main__":
    ok = run()
    raise SystemExit(0 if ok else 1)
