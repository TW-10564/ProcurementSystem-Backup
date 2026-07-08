"""M1 build entrypoint: ingest sources → registry → plan values → Gate A report.

Run:  cd app/backend && python3 -m onefcst.build
"""
from __future__ import annotations

from datetime import date, datetime

from .config import ONE_MASTER, NOTE_ONE_MASTER, planning_file
from .db import init_db, get_session, engine
from .models import Base, Snapshot, PlanValue, StockState, Actual, Item
from .ingestion import one_master, notepc_forecast, planning4, planning1, subfiles, sap
from .ingestion.excel import file_meta
from .registry.build import (build_registry, resolve,
                             build_normalized_alias_index, resolve_normalized)

import sys
from pathlib import Path as _Path
_CONSOLIDATION = _Path(__file__).resolve().parents[3] / "consolidation"
if str(_CONSOLIDATION) not in sys.path:
    sys.path.insert(0, str(_CONSOLIDATION))
from config_all import FILES as SUBFILE_CFG, BASE as SUBFILE_BASE   # noqa: E402


def _snapshot(session, path, sheet, rows, status, note=""):
    meta = file_meta(path)
    snap = Snapshot(source_file=path.name, sheet=sheet, file_mtime=meta["mtime"],
                    checksum=meta["checksum"], row_count=rows,
                    gate_status=status, gate_note=note)
    session.add(snap)
    session.flush()
    return snap


def _resolve_planning1(session, norm_index, name):
    """FIX_PLAN M4: try each documented naming-convention variant (see
    ingestion/planning1.candidate_variants) against both exact and
    normalized (width/spacing-insensitive) alias resolution before giving
    up. Returns (item, used_variant) — used_variant lets the caller report
    how many resolutions needed a variant vs matched as-is."""
    for variant in planning1.candidate_variants(name):
        item = resolve(session, variant)
        if item is None:
            item = resolve_normalized(norm_index, variant)
        if item is not None:
            return item, (variant != name)
    return None, False


def _ingest_plan_rows(session, snap, rows, level, resolved_counter):
    unresolved = 0
    fuzzy_matched = 0
    norm_index = build_normalized_alias_index(session) if level == "planning1" else None
    for p in rows:
        name = p.get("raw_name") or p.get("adana")
        if level == "planning1":
            item = resolve(session, name)
            if item is None:
                item, _ = _resolve_planning1(session, norm_index, name)
                if item is not None:
                    fuzzy_matched += 1
        else:
            item = resolve(session, name)
        if item is None:
            unresolved += 1
        session.add(PlanValue(snapshot_id=snap.id, item_id=item.id if item else None,
                              raw_name=name, month=p["month"], level=level,
                              value=p["value"], source_cell=p.get("cell")))
    # accumulate, not overwrite — two sources (desktop + NotePC 部品予測)
    # both report under level="master", and a plain assignment here would
    # silently discard whichever ran first from the Gate A report
    prev_res, prev_tot = resolved_counter.get(level, (0, 0))
    resolved_counter[level] = (prev_res + len(rows) - unresolved, prev_tot + len(rows))
    if level == "planning1" and fuzzy_matched:
        print(f"[①販売計画] {fuzzy_matched} rows recovered where plain exact matching "
              f"failed — via width/spacing normalization or a naming-convention "
              f"variant (ASR/RTX prefix, GB→G)")
    return unresolved


def run() -> dict:
    print(f"=== onefcst M1 build  {datetime.now():%Y-%m-%d %H:%M} ===")
    Base.metadata.drop_all(engine)       # POC: full rebuild each run
    init_db()

    report = {"registry": {}, "levels": {}, "subfiles": [], "sap": {},
             "gate_a": {}, "conflicts": []}

    # 1. registry
    stats = build_registry()
    report["registry"] = stats
    report["conflicts"] = stats.pop("conflict_rows", [])
    print(f"[registry] items={stats['items']}  aliases={stats['aliases']}  "
          f"conflicts={stats['alias_conflicts']}  "
          f"master-attr hits={stats['master_attr_hits']}  "
          f"note models={stats['note_models']}  "
          f"④aliases={stats['plan4_aliases']} (unresolved {stats['unresolved_plan4']})")

    session = get_session()
    resolved_counter: dict[str, tuple] = {}

    # 2. master plan values (level='master')
    fc = one_master.parse_parts_forecast()
    snap = _snapshot(session, ONE_MASTER, "部品予測", len(fc["plan"]), "PASS")
    unresolved = _ingest_plan_rows(session, snap, fc["plan"], "master", resolved_counter)
    print(f"[master 部品予測] plan cells={len(fc['plan'])}  "
          f"months={len(fc['months'])} ({fc['months'][0]}→{fc['months'][-1]})  "
          f"unresolved rows={unresolved}")

    # 2b. NotePC master plan values (level='master', same level as desktop —
    # so the SKU forecasting engine picks these up with zero engine changes;
    # found 2026-07-07 that NoteONE計画's own 部品予測 sheet was never read)
    notepc_fc = notepc_forecast.parse_notepc_forecast()
    notepc_snap = _snapshot(session, NOTE_ONE_MASTER, "部品予測",
                            len(notepc_fc["plan"]), "PASS")
    notepc_unresolved = _ingest_plan_rows(session, notepc_snap, notepc_fc["plan"],
                                          "master", resolved_counter)
    print(f"[NotePC 部品予測] plan cells={len(notepc_fc['plan'])}  "
         f"months={len(notepc_fc['months'])} "
         f"({notepc_fc['months'][0]}→{notepc_fc['months'][-1]})  "
         f"unresolved rows={notepc_unresolved}")

    # 3. planning ④ paste (level='planning4')
    p4 = [p for p in planning4.parse_one_paste() if p["month"] and p["value"] is not None]
    f4 = planning_file("④")
    snap4 = _snapshot(session, f4, "その他ONE貼り付け", len(p4), "PASS")
    unresolved4 = _ingest_plan_rows(session, snap4, p4, "planning4", resolved_counter)
    print(f"[④ONE貼り付け] rows={len(p4)}  unresolved={unresolved4}")

    # 4. planning ①販売計画 (level='planning1') — model-level seed, pre-attach
    p1 = planning1.parse_plan1()
    f1 = planning_file("①")
    snap1 = _snapshot(session, f1, "コンシューマー+コマーシャル", len(p1), "PASS")
    unresolved1 = _ingest_plan_rows(session, snap1, p1, "planning1", resolved_counter)
    print(f"[①販売計画] rows={len(p1)}  unresolved={unresolved1}  "
          f"(model names rarely match 綽名 directly — expected, needs ﾏｽﾀ bridge)")

    # 5. sub-file 部品詳細 series (level='one_row') + stock snapshot
    total_series, total_stock = 0, 0
    for cat in subfiles.all_categories():
        result = subfiles.parse_subfile_series(cat, snapshot_date=date.today())
        st = result["stats"]
        report["subfiles"].append(st)
        if not result["series"] and not result["stock"]:
            print(f"  [{cat:<11}] SKIP — {st['note']}")
            continue
        cfgpath = SUBFILE_CFG[cat]
        srcpath = _Path(cfgpath.get("_abspath") or (SUBFILE_BASE / cfgpath["file"]))
        snap_s = _snapshot(session, srcpath, "部品詳細",
                           len(result["series"]) + len(result["stock"]), "PASS", st["note"])
        for p in result["series"]:
            item = resolve(session, p["adana"])
            session.add(PlanValue(snapshot_id=snap_s.id, item_id=item.id if item else None,
                                  raw_name=p["adana"], month=p["month"], level="one_row",
                                  value=p["value"], source_cell=p["cell"]))
        for s in result["stock"]:
            item = resolve(session, s["adana"])
            session.add(StockState(item_id=item.id if item else None, raw_name=s["adana"],
                                   snapshot_date=s["snapshot"], one_balance=s["balance"],
                                   one_stock_days=s["days"], safety_days=s["safety_days"],
                                   snapshot_id=snap_s.id))
        total_series += len(result["series"])
        total_stock += len(result["stock"])
        print(f"  [{cat:<11}] skus={st['skus']:>3}/{st['expected_skus']:<3} "
              f"series_cells={len(result['series']):>5}  stock_rows={len(result['stock']):>3}")
    print(f"[sub-files] total series cells={total_series}  stock rows={total_stock}")

    # 6. SAP physical stock
    sap_result = sap.parse_sap_stock()
    report["sap"] = {"path": str(sap_result["path"]) if sap_result["path"] else None,
                     "matched": 0, "rows": len(sap_result["rows"]),
                     "unnamed": sap_result["unnamed"],
                     "resolved_via_crosswalk": sap_result["resolved_via_crosswalk"]}
    if sap_result["path"]:
        matched = 0
        matched_via_normalization = 0
        norm_index = None   # built lazily, only if an exact match ever misses
        for adana, qty in sap_result["rows"].items():
            item = resolve(session, adana)
            if item is None:
                # FIX_PLAN M3: exact-string resolve() leaves a row unmatched
                # when the あだ名 (direct or crosswalk-recovered) differs from
                # the stored ONE alias only by width/spacing/hyphenation.
                if norm_index is None:
                    norm_index = build_normalized_alias_index(session)
                item = resolve_normalized(norm_index, adana)
                if item is not None:
                    matched_via_normalization += 1
            if item:
                matched += 1
            session.add(StockState(item_id=item.id if item else None, raw_name=adana,
                                   snapshot_date=sap_result["month"], sap_qty=qty))
        report["sap"]["matched"] = matched
        report["sap"]["matched_via_normalization"] = matched_via_normalization
        print(f"[SAP] file={sap_result['path'].name}  あだ名={len(sap_result['rows'])}  "
              f"matched={matched} (of which {matched_via_normalization} via "
              f"width/spacing normalization)  unnamed_rows={sap_result['unnamed']}  "
              f"resolved_via_name_crosswalk={sap_result['resolved_via_crosswalk']}")
    else:
        print("[SAP] no stock file found (glob miss) — skipped, non-blocking for M1")

    session.commit()

    # 7. Gate A summary
    n_pv = session.query(PlanValue).count()
    n_linked = session.query(PlanValue).filter(PlanValue.item_id.isnot(None)).count()
    rate = 100 * n_linked / max(1, n_pv)
    n_t1 = session.query(Item).filter(Item.tier == 1).count()
    n_t2 = session.query(Item).filter(Item.tier == 2).count()
    report["gate_a"] = {"plan_values": n_pv, "resolved": n_linked, "rate": rate,
                        "tier1_items": n_t1, "tier2_items": n_t2,
                        "by_level": resolved_counter}
    print(f"[gate A] plan values={n_pv}  resolved-to-item={n_linked} ({rate:.1f}%)  "
          f"tier1 items={n_t1}  tier2 items={n_t2}")
    for lvl, (res, tot) in resolved_counter.items():
        print(f"          {lvl:<10} {res}/{tot} resolved ({100*res/max(1,tot):.1f}%)")
    status = "PASS" if rate >= 95 else "WARN"
    report["gate_a"]["status"] = status
    print(f"[gate A] {status}" + ("" if status == "PASS" else
          " — overall resolution below 95% (expected: ①/planning1 has low "
          "resolution since model names ≠ 綽名 without the alias bridge; "
          "master/④/one_row should each be ~100%)"))
    session.close()
    return report


if __name__ == "__main__":
    run()
