"""検証 workbook — every M1 number, checkable in Excel.

Sheets:
  Gate A Summary     resolution rates by level, item/alias counts
  Alias Conflicts    (alias, system, kept vs dropped item) — review queue seed
  Unresolved Names   raw_name/level/count for anything not linked to an item
  Plan Diff (3-level) ④ vs sub-file(one_row) vs master, per item x month —
                     surfaces the measured divergence as data, not a claim
  Snapshot Inventory  every source file ingested, with mtime/checksum/status

Read-only against sources; this workbook is a NEW file under app/data/exports/.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func

from ..config import EXPORT_DIR
from ..db import get_session
from ..models import Item, ItemAlias, PlanValue, Snapshot

HEADER_FILL = PatternFill("solid", fgColor="1D4ED8")
HEADER_FONT = Font(color="FFFFFF", bold=True)
DIVERGE_FILL = PatternFill("solid", fgColor="FBEEE6")


def _header(ws, cols):
    ws.append(cols)
    for c in ws[1]:
        c.font = HEADER_FONT
        c.fill = HEADER_FILL


def build(report: dict) -> Path:
    wb = openpyxl.Workbook()
    session = get_session()

    # -- Gate A Summary --------------------------------------------------
    ws = wb.active
    ws.title = "Gate A Summary"
    _header(ws, ["metric", "value"])
    g = report["gate_a"]
    ws.append(["items total", report["registry"]["items"]])
    ws.append(["tier1 items (planning: VGA/CPU/MB/Case)", g["tier1_items"]])
    ws.append(["tier2 items (derived)", g["tier2_items"]])
    ws.append(["aliases total", report["registry"]["aliases"]])
    ws.append(["alias conflicts (quarantined)", report["registry"]["alias_conflicts"]])
    ws.append(["plan values ingested", g["plan_values"]])
    ws.append(["plan values resolved to item", g["resolved"]])
    ws.append(["overall resolution rate %", round(g["rate"], 1)])
    ws.append(["status", g["status"]])
    ws.append([])
    _header(ws, ["level", "resolved", "total", "rate %"])
    for lvl, (res, tot) in g["by_level"].items():
        ws.append([lvl, res, tot, round(100 * res / max(1, tot), 1)])
    if report.get("sap", {}).get("path"):
        ws.append([])
        ws.append(["SAP file", report["sap"]["path"]])
        ws.append(["SAP あだ名 rows", report["sap"]["rows"]])
        ws.append(["SAP matched to item", report["sap"]["matched"]])
        ws.append(["SAP unnamed rows (excluded)", report["sap"]["unnamed"]])
    for col, w in zip("AB", (48, 16)):
        ws.column_dimensions[col].width = w

    # -- Alias Conflicts ---------------------------------------------------
    ws = wb.create_sheet("Alias Conflicts")
    _header(ws, ["alias", "source_system", "kept_item_id", "kept_name",
                "dropped_item_id", "review_needed"])
    id_to_name = {i.id: i.canonical_name for i in session.query(Item).all()}
    for c in report.get("conflicts", []):
        ws.append([c["alias"], c["system"], c["kept_item_id"],
                  id_to_name.get(c["kept_item_id"], ""), c["dropped_item_id"], "YES"])
    for col, w in zip("ABCDEF", (24, 16, 12, 28, 16, 14)):
        ws.column_dimensions[col].width = w

    # -- Unresolved Names ----------------------------------------------------
    ws = wb.create_sheet("Unresolved Names")
    _header(ws, ["level", "raw_name", "occurrences"])
    unresolved_q = (session.query(PlanValue.level, PlanValue.raw_name,
                                  func.count(PlanValue.id))
                    .filter(PlanValue.item_id.is_(None))
                    .group_by(PlanValue.level, PlanValue.raw_name)
                    .order_by(PlanValue.level, func.count(PlanValue.id).desc()))
    for lvl, name, n in unresolved_q:
        ws.append([lvl, name, n])
    for col, w in zip("ABC", (14, 34, 14)):
        ws.column_dimensions[col].width = w

    # -- Plan Diff (3-level) --------------------------------------------------
    ws = wb.create_sheet("Plan Diff (3-level)")
    _header(ws, ["item", "category", "month", "planning4 (④)", "one_row (sub-file)",
                "master (ONE計画)", "master - planning4", "% diff", "note"])
    # last 3 months + forward: recent enough to carry live signal, wide enough
    # to include the month the planning-side snapshot (④) was last pasted from.
    _cy, _cm = date.today().year, date.today().month - 3
    while _cm <= 0:
        _cy, _cm = _cy - 1, _cm + 12
    cutoff = date(_cy, _cm, 1)
    rows = (session.query(PlanValue.item_id, PlanValue.level, PlanValue.month,
                          func.sum(PlanValue.value))
            .filter(PlanValue.item_id.isnot(None))
            .filter(PlanValue.level.in_(["planning4", "one_row", "master"]))
            .filter(PlanValue.month >= cutoff)
            .group_by(PlanValue.item_id, PlanValue.level, PlanValue.month)
            .all())
    by_key: dict[tuple, dict] = {}
    for item_id, level, month, val in rows:
        by_key.setdefault((item_id, month), {})[level] = val
    items = {i.id: i for i in session.query(Item).all()}
    out_rows, n_diverge = [], 0
    for (item_id, month), levels in by_key.items():
        p4, orow, mst = levels.get("planning4"), levels.get("one_row"), levels.get("master")
        if not any(v not in (None, 0) for v in (p4, orow, mst)):
            continue      # skip rows with no real signal at all
        item = items.get(item_id)
        diff = (mst - p4) if (mst is not None and p4 is not None) else None
        pct = (100 * diff / p4) if diff not in (None, 0) and p4 else None
        note = ""
        if diff is not None and abs(diff) > 0.5:
            n_diverge += 1
            note = "DIVERGES — adjustment or plan revision (see [[one-master-architecture]])"
        out_rows.append((abs(diff) if diff else -1, item, month, p4, orow, mst, diff, pct, note))
    # diverging + highest-magnitude rows first, so opening the sheet shows signal immediately
    out_rows.sort(key=lambda r: (-1 if r[8] else 0, -(r[0] or 0), r[2]))
    for _, item, month, p4, orow, mst, diff, pct, note in out_rows:
        ws.append([item.canonical_name if item else "?", item.category if item else "",
                  month.isoformat(),
                  round(p4, 1) if p4 is not None else None,
                  round(orow, 1) if orow is not None else None,
                  round(mst, 1) if mst is not None else None,
                  round(diff, 1) if diff is not None else None,
                  round(pct, 1) if pct is not None else None, note])
        if note:
            for cell in ws[ws.max_row]:
                cell.fill = DIVERGE_FILL
    ws.freeze_panes = "A2"
    for col, w in zip("ABCDEFGHI", (22, 16, 12, 14, 16, 14, 14, 10, 44)):
        ws.column_dimensions[col].width = w

    # -- Snapshot Inventory ----------------------------------------------------
    ws = wb.create_sheet("Snapshot Inventory")
    _header(ws, ["source_file", "sheet", "file_mtime", "row_count",
                "gate_status", "note"])
    for s in session.query(Snapshot).order_by(Snapshot.source_file).all():
        ws.append([s.source_file, s.sheet,
                  s.file_mtime.isoformat() if s.file_mtime else None,
                  s.row_count, s.gate_status, s.gate_note or ""])
    for col, w in zip("ABCDEF", (34, 24, 20, 12, 12, 40)):
        ws.column_dimensions[col].width = w

    session.close()
    out = EXPORT_DIR / f"検証_{date.today():%Y%m%d}.xlsx"
    wb.save(out)
    print(f"[verify] {n_diverge} diverging item-months in 3-level plan diff")
    print(f"[verify] workbook → {out}")
    return out


if __name__ == "__main__":
    from ..build import run
    build(run())
