"""M1 build entrypoint: ingest sources → registry → plan values → Gate A report.

Run:  cd app/backend && python3 -m onefcst.build
"""
from __future__ import annotations

from datetime import date, datetime

from .config import ONE_MASTER, NOTE_ONE_MASTER, planning_file
from .db import init_db, get_session, engine
from .models import (Base, Snapshot, PlanValue, StockState, Actual, WeeklyActual,
                     Item, ItemAlias)
from .ingestion import (one_master, notepc_forecast, planning4, planning1, subfiles, sap,
                       sap_shipments, sap_stock_history, sap_zp21)
from .ingestion.sap_shipment_overrides import resolve_shipment_override
from .ingestion.excel import file_meta
from .ingestion.subfile_config import FILES as SUBFILE_CFG, BASE as SUBFILE_BASE
from .registry.build import (build_registry, resolve, resolve_via_source,
                             build_normalized_alias_index, resolve_any,
                             _norm_for_crosswalk)

from pathlib import Path as _Path


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
    ingestion/planning1.candidate_variants) via the standard resolve_any
    (exact, then normalized) before giving up. Returns (item, used_variant)
    — used_variant lets the caller report how many resolutions needed a
    variant vs matched as-is (exact or normalized alone).

    ④企画名前 checked first for each variant too, not just the un-stripped
    original — the real "B650" collision (see resolve_via_source) is only
    reachable through this loop: "ASR B650" isn't itself an alias anywhere,
    it's candidate_variants() stripping "ASR" that produces the exact
    string "B650" that then collides across two items."""
    for variant in planning1.candidate_variants(name):
        item = (resolve_via_source(session, variant, "④企画名前")
                or resolve_any(session, norm_index, variant))
        if item is not None:
            return item, (variant != name)
    return None, False


def _ingest_plan_rows(session, norm_index, snap, rows, level, resolved_counter):
    unresolved = 0
    fuzzy_matched = 0
    for p in rows:
        name = p.get("raw_name") or p.get("adana")
        if level == "planning1":
            # ④企画名前 (the curated planning-name bridge) checked first, not
            # resolve()'s source-agnostic pick — see resolve_via_source's
            # docstring for the real "B650" PSU-vs-motherboard collision
            # this avoids.
            item = resolve_via_source(session, name, "④企画名前") or resolve(session, name)
            if item is None:
                item, _ = _resolve_planning1(session, norm_index, name)
                if item is not None:
                    fuzzy_matched += 1
        else:
            item = resolve_any(session, norm_index, name)
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


def _resolve_shipment_row(session, norm_index, row):
    """name_a (読みかえ output) first, name_b (raw SAP code) fallback, then
    the verified manual override table (see ingestion/sap_shipment_overrides.py)
    for raw labels known to use a naming variant (e.g. a "v2"/色 suffix)
    the catalog and name_a/name_b matching don't otherwise resolve —
    see ingestion/sap_shipments.py's module docstring."""
    item = None
    if row["name_a"] and row["name_a"] != "該当無し":
        item = resolve_any(session, norm_index, row["name_a"])
    if item is None and row["name_b"]:
        item = resolve_any(session, norm_index, str(row["name_b"]))
    if item is None:
        override_target = (resolve_shipment_override(row["name_a"])
                            or resolve_shipment_override(row["name_b"]))
        if override_target:
            item = resolve_any(session, norm_index, override_target)
    return item


def _ingest_sap_shipments(session, norm_index, agg_rows, weekly_rows=None) -> dict:
    """Resolves each aggregated shipment row and writes one Actual row per
    (item, month) — plus, when weekly_rows is given (name_a/name_b-aligned
    with agg_rows, one row per item, see sap_shipments.flatten_weekly), one
    WeeklyActual row per (item, week).

    WeeklyActual is no longer fed from here as of 2026-07-23 — build.run()
    stopped passing weekly_rows (see the call site below). Per procurement's
    own explanation, this source's timestamp (PC-shipment date) is the
    wrong one for plan-vs-actual; ingestion/sap_zp21.py's 組込 ("build
    incorporation") data, keyed to the timing purchasing actually tracks
    demand by, replaced it there. The weekly_rows parameter and branch are
    left in place (not deleted) since this function is still directly unit
    tested against both behaviors — see tests/test_sap_shipments.py.

    Extracted from run() for direct unit testing
    (build.run() itself needs live NAS access — see _resolve_planning1/
    _ingest_plan_rows above for the same pattern).

    Found live 2026-07-15 against the real sheet: TWO DISTINCT rows can
    legitimately resolve to the SAME catalog item — e.g. 'RX7600XT' (name_a
    exact match) and a separate '該当無し'/'RX7600XT' row (name_b fallback
    match) both resolved to item 827. That's real: the source sheet tracks
    some parts across more than one line (different brand/channel), and
    both lines' shipped quantity is real demand for the one item we track.
    Summing by (item_id, month) / (item_id, week_start) BEFORE writing is
    required — inserting one row per source row would violate Actual's/
    WeeklyActual's own (item_id, month-or-week, source) uniqueness the
    moment two rows collide like this, taking down the entire rebuild over
    what is actually correct, summable data."""
    resolved, unresolved = 0, 0
    monthly_totals: dict[tuple[int, object], float] = {}
    weekly_totals: dict[tuple[int, object], float] = {}
    weekly_rows = weekly_rows or [None] * len(agg_rows)
    for row, wrow in zip(agg_rows, weekly_rows):
        item = _resolve_shipment_row(session, norm_index, row)
        if item is None:
            unresolved += 1
            continue
        resolved += 1
        for month, qty in row["months"].items():
            key = (item.id, month)
            monthly_totals[key] = monthly_totals.get(key, 0.0) + qty
        if wrow is not None:
            for week_start, qty in wrow["weeks"].items():
                key = (item.id, week_start)
                weekly_totals[key] = weekly_totals.get(key, 0.0) + qty

    for (item_id, month), qty in monthly_totals.items():
        session.add(Actual(item_id=item_id, month=month, qty=qty, source="SAP"))
    for (item_id, week_start), qty in weekly_totals.items():
        session.add(WeeklyActual(item_id=item_id, week_start=week_start,
                                 qty=qty, source="SAP"))
    return {"resolved": resolved, "unresolved": unresolved}


class RequiredSourceUnreachable(Exception):
    """Raised by _check_required_sources when one or more mandatory NAS
    inputs can't be read — before run() has touched the DB at all."""


def _check_required_sources() -> None:
    """Verify every source run() cannot proceed without is actually
    reachable, BEFORE Base.metadata.drop_all() runs.

    Found live 2026-07-15 (twice, in the same session): the NAS mount
    being unreachable (no `Z:` drive / `/mnt/z` mounted) was previously
    only discovered deep inside one_master.parse_parts_forecast() or
    planning_file()'s own FileNotFoundError — AFTER drop_all() had
    already destroyed every table, including all of rebuild_preserving.py's
    human-entered data (Adjustment/CompatRule/Actual/WeeklyActual/etc,
    which that module exists specifically to protect ACROSS a successful
    rebuild — it does nothing for a rebuild that never gets that far).
    Checking reachability first means a missing/unmounted NAS fails loud
    and immediate, with the existing DB completely untouched — the
    now-familiar rule "never let a partial/failed operation destroy more
    than it accomplishes" (matches the rebuild-safe-persistence design
    intent in PILOT_DEPLOY_PLAN.md's blocker #1)."""
    missing = []
    if not ONE_MASTER.exists():
        missing.append(str(ONE_MASTER))
    if not NOTE_ONE_MASTER.exists():
        missing.append(str(NOTE_ONE_MASTER))
    for prefix in ("④", "①"):
        try:
            planning_file(prefix)
        except FileNotFoundError as e:
            missing.append(str(e))
    if missing:
        raise RequiredSourceUnreachable(
            "Required NAS source(s) unreachable — aborting before touching "
            "the database (nothing was dropped or modified):\n  " +
            "\n  ".join(missing))


def run() -> dict:
    print(f"=== onefcst M1 build  {datetime.now():%Y-%m-%d %H:%M} ===")
    _check_required_sources()
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
    if stats.get("stock_policy_source"):
        print(f"[在庫の持ち方] source={stats['stock_policy_source'].split('/')[-1]}  "
              f"min_stock_days filled={stats['stock_policy_fallback_filled']}"
              f"  skipped labels={stats['stock_policy_skipped']}")
    else:
        print("[在庫の持ち方] no policy file found (glob miss) — skipped, non-blocking")
    print(f"[no-reorder flag] gray-cell marking read from 6 .xlsx categories  "
          f"items flagged={stats['no_reorder_items_filled']}")
    print(f"[lead time] real observed order→delivery median (購買計画, "
          f"delivered POs, >=3 samples)  items filled={stats['observed_lead_time_items_filled']}")

    session = get_session()
    resolved_counter: dict[str, tuple] = {}

    # Standardized mapping (2026-07-08): ONE normalized-alias index, built
    # once right after the registry commits all aliases, threaded through
    # EVERY ingestion path below (master, ④, ①, sub-files, SAP) via
    # resolve_any. Previously each path grew its own copy of "try exact,
    # then fall back to width/spacing/case-insensitive matching" — SAP and
    # ①販売計画 had it, master/④/sub-files/SAP didn't, so a name that only
    # differed by case or fullwidth-vs-halfwidth (e.g. a CPU recorded as
    # 'Ry53400G' in one file and 'ry53400G' in another) silently failed to
    # resolve depending on which ingestion path happened to see it first.
    # One shared index + one shared resolver means every future monthly
    # rebuild gets the same matching quality everywhere, automatically.
    norm_index = build_normalized_alias_index(session)

    # 2. master plan values (level='master')
    fc = one_master.parse_parts_forecast()
    snap = _snapshot(session, ONE_MASTER, "部品予測", len(fc["plan"]), "PASS")
    unresolved = _ingest_plan_rows(session, norm_index, snap, fc["plan"], "master",
                                   resolved_counter)
    print(f"[master 部品予測] plan cells={len(fc['plan'])}  "
          f"months={len(fc['months'])} ({fc['months'][0]}→{fc['months'][-1]})  "
          f"unresolved rows={unresolved}")

    # 2b. NotePC master plan values (level='master', same level as desktop —
    # so the SKU forecasting engine picks these up with zero engine changes;
    # found 2026-07-07 that NoteONE計画's own 部品予測 sheet was never read)
    notepc_fc = notepc_forecast.parse_notepc_forecast()
    notepc_snap = _snapshot(session, NOTE_ONE_MASTER, "部品予測",
                            len(notepc_fc["plan"]), "PASS")
    notepc_unresolved = _ingest_plan_rows(session, norm_index, notepc_snap,
                                          notepc_fc["plan"], "master", resolved_counter)
    print(f"[NotePC 部品予測] plan cells={len(notepc_fc['plan'])}  "
         f"months={len(notepc_fc['months'])} "
         f"({notepc_fc['months'][0]}→{notepc_fc['months'][-1]})  "
         f"unresolved rows={notepc_unresolved}")

    # 3. planning ④ paste (level='planning4')
    p4 = [p for p in planning4.parse_one_paste() if p["month"] and p["value"] is not None]
    f4 = planning_file("④")
    snap4 = _snapshot(session, f4, "その他ONE貼り付け", len(p4), "PASS")
    unresolved4 = _ingest_plan_rows(session, norm_index, snap4, p4, "planning4",
                                    resolved_counter)
    print(f"[④ONE貼り付け] rows={len(p4)}  unresolved={unresolved4}")

    # 4. planning ①販売計画 (level='planning1') — model-level seed, pre-attach
    p1 = planning1.parse_plan1()
    f1 = planning_file("①")
    snap1 = _snapshot(session, f1, "コンシューマー+コマーシャル", len(p1), "PASS")
    unresolved1 = _ingest_plan_rows(session, norm_index, snap1, p1, "planning1",
                                    resolved_counter)
    print(f"[①販売計画] rows={len(p1)}  unresolved={unresolved1}  "
          f"(model names rarely match 綽名 directly — expected, needs ﾏｽﾀ bridge)")

    # 5. sub-file 部品詳細 series (level='one_row') + stock snapshot
    total_series, total_stock = 0, 0
    subfile_only_created = 0
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
        # Pre-pass: register SKUs that exist ONLY in this sub-file. Found
        # 2026-07-27 via the FAN file: 静音パック(AMD)/(Intel) (~12.5k units
        # of stock each) and a CAHPS-M2+Heatsink combo row live only in
        # 部品詳細, never in the master's 部品予測 — the registry (built
        # from master sources) had no item for them, so their series and
        # stock rows below were stored with item_id=None, invisible to
        # every view and never forecasted. Create a tier-2 item per such
        # SKU (category = this sub-file's key, subcategory 'subfile-only'
        # so they stay distinguishable from master-backed items). Only
        # rows carrying real data qualify — a name with no series values
        # and no stock balance is a placeholder row, not a part.
        has_data: set[str] = {p["adana"] for p in result["series"]
                              if p["value"] is not None}
        has_data |= {srow["adana"] for srow in result["stock"]
                     if srow["balance"] is not None}
        created_here = 0
        for adana in sorted(has_data):
            if resolve_any(session, norm_index, adana,
                           prefer_source="ONE綽名") is not None:
                continue
            new_item = Item(canonical_name=adana, kind="part", category=cat,
                            subcategory="subfile-only", tier=2)
            session.add(new_item)
            session.flush()
            session.add(ItemAlias(item_id=new_item.id, alias=adana,
                                  source_system="ONE綽名"))
            nkey = _norm_for_crosswalk(adana)
            if nkey and nkey not in norm_index:
                norm_index[nkey] = new_item
            created_here += 1
        if created_here:
            subfile_only_created += created_here
            print(f"  [{cat:<11}] registered {created_here} subfile-only SKU(s) "
                  f"absent from the master registry")
        for p in result["series"]:
            item = resolve_any(session, norm_index, p["adana"], prefer_source="ONE綽名")
            session.add(PlanValue(snapshot_id=snap_s.id, item_id=item.id if item else None,
                                  raw_name=p["adana"], month=p["month"], level="one_row",
                                  value=p["value"], source_cell=p["cell"]))
        for s in result["stock"]:
            item = resolve_any(session, norm_index, s["adana"], prefer_source="ONE綽名")
            session.add(StockState(item_id=item.id if item else None, raw_name=s["adana"],
                                   snapshot_date=s["snapshot"], one_balance=s["balance"],
                                   one_stock_days=s["days"], safety_days=s["safety_days"],
                                   snapshot_id=snap_s.id))
        total_series += len(result["series"])
        total_stock += len(result["stock"])
        print(f"  [{cat:<11}] skus={st['skus']:>3}/{st['expected_skus']:<3} "
              f"series_cells={len(result['series']):>5}  stock_rows={len(result['stock']):>3}")
        if st.get("excluded_incomplete_months"):
            print(f"    excluded partial-coverage months (file's own column "
                  f"range clips into these): {', '.join(st['excluded_incomplete_months'])}")
    print(f"[sub-files] total series cells={total_series}  stock rows={total_stock}  "
          f"subfile-only items registered={subfile_only_created}")
    report["registry"]["subfile_only_items_created"] = subfile_only_created

    # 6. SAP physical stock
    sap_result = sap.parse_sap_stock()
    report["sap"] = {"path": str(sap_result["path"]) if sap_result["path"] else None,
                     "matched": 0, "rows": len(sap_result["rows"]),
                     "resolved_via_crosswalk": sap_result["resolved_via_crosswalk"]}
    if sap_result["path"]:
        # SYOCODE is the primary resolution key (standardized 2026-07-09 —
        # see ingestion/sap.py docstring): it lines up with 商品コード
        # ItemAlias rows already in the registry, and resolves more blank-
        # あだ名 rows than the SYONAME crosswalk alone. あだ名 is the fallback
        # for rows whose SYOCODE isn't itself a known 商品コード. Aggregation
        # happens here (by resolved item), not in sap.py, since two raw rows
        # with different あだ名 spellings can resolve to the same item.
        agg: dict = {}
        matched_via_syocode = 0
        matched_via_adana = 0
        unnamed = 0
        for row in sap_result["rows"]:
            item = None
            if row["syocode"]:
                item = resolve_any(session, norm_index, row["syocode"], prefer_source="商品コード")
                if item is not None:
                    matched_via_syocode += 1
            if item is None and row["adana"]:
                item = resolve_any(session, norm_index, row["adana"], prefer_source="ONE綽名")
                if item is not None:
                    matched_via_adana += 1
            if item is None:
                unnamed += 1
            key = item.id if item is not None else ("unresolved", row["adana"] or row["syocode"])
            entry = agg.setdefault(key, {"item": item,
                                         "raw_name": row["adana"] or row["syocode"] or "?",
                                         "qty": 0.0})
            entry["qty"] += row["qty"]
        matched = 0
        for entry in agg.values():
            item = entry["item"]
            if item is not None:
                matched += 1
            session.add(StockState(item_id=item.id if item else None, raw_name=entry["raw_name"],
                                   snapshot_date=sap_result["as_of_date"], sap_qty=entry["qty"]))
        report["sap"]["matched"] = matched
        report["sap"]["matched_via_syocode"] = matched_via_syocode
        report["sap"]["matched_via_adana"] = matched_via_adana
        report["sap"]["unnamed"] = unnamed
        print(f"[SAP] file={sap_result['path'].name}  rows={len(sap_result['rows'])}  "
              f"resolved_items={matched}/{len(agg)} (via SYOCODE={matched_via_syocode}, "
              f"via あだ名={matched_via_adana})  unnamed_rows={unnamed}  "
              f"resolved_via_name_crosswalk={sap_result['resolved_via_crosswalk']}")
    else:
        print("[SAP] no stock file found (glob miss) — skipped, non-blocking for M1")

    # 6b. SAP stock HISTORY backfill — manually-placed weekly raw ZM13
    # exports (ingestion/sap_stock_history.py), distinct from the single
    # "current" file/accdb read above. Added 2026-07-23 so the
    # zero-stock discontinuation signal (verify/discontinuation.py,
    # MIN_ZERO_STOCK_DAYS=60/MIN_ZERO_SNAPSHOTS=2) has enough accumulated
    # history to ever fire, instead of waiting ~9 real weekly rebuild
    # cycles. Writes one StockState row per (item, file's own as-of date)
    # — same sap_qty field the "current" SAP path above writes, so
    # forecast_views._prefer_sap_row/_stock_by_item treat these
    # identically to a real weekly SAP refresh once they're in the DB.
    # Preserved across future onefcst.rebuild_preserving runs the same
    # way WeeklyActual is (see rebuild_preserving.py).
    stock_history = sap_stock_history.parse_all_stock_history()
    report["sap_stock_history"] = {"snapshots": len(stock_history), "matched": {}, "total_items": {}}
    for entry in stock_history:
        agg: dict = {}
        matched = 0
        for row in entry["rows"]:
            item = resolve_any(session, norm_index, row["code"], prefer_source="商品コード")
            if item is None and row["name_text"]:
                item = resolve_any(session, norm_index, row["name_text"], prefer_source="ONE綽名")
            if item is not None:
                matched += 1
            key = item.id if item is not None else ("unresolved", row["code"])
            e = agg.setdefault(key, {"item": item, "raw_name": row["name_text"] or row["code"], "qty": 0.0})
            e["qty"] += row["qty"]
        for e in agg.values():
            item = e["item"]
            session.add(StockState(item_id=item.id if item else None, raw_name=e["raw_name"],
                                   snapshot_date=entry["as_of_date"], sap_qty=e["qty"]))
        report["sap_stock_history"]["matched"][str(entry["as_of_date"])] = matched
        report["sap_stock_history"]["total_items"][str(entry["as_of_date"])] = len(agg)
        print(f"[SAP stock history] {entry['path'].name}  as_of={entry['as_of_date']}  "
              f"resolved_items={matched}/{len(agg)}")
    if not stock_history:
        print("[SAP stock history] no dated backfill files found — skipped, non-blocking")

    # 6c. SAP shipments (実売実績) → Actual (MONTHLY only) — found
    # 2026-07-15: the Actual table (real per-item sales, read by FVA
    # scoring and the Forecast page's recency sort) had ZERO rows in the
    # live DB before this step; nothing had ever written to it. See
    # ingestion/sap_shipments.py. No longer feeds WeeklyActual as of
    # 2026-07-23 — see sap_zp21 step below and _ingest_sap_shipments'
    # docstring for why.
    #
    # Wrapped defensively (found live 2026-07-15, first real run against
    # unfiltered NAS data: an IntegrityError here — since fixed — crashed
    # the ENTIRE rebuild, discarding the master/④/①/sub-file/SAP-stock
    # work already done in this same uncommitted session, since run()
    # commits once at the end. This is genuinely optional/best-effort
    # data (same tier as the SAP stock file's own "missing = skip,
    # non-blocking" handling above) — a problem specific to this newest,
    # least-proven ingestion path must not be able to take the whole
    # rebuild down with it.
    report["sap_shipments"] = {"path": None, "rows": 0, "resolved": 0, "unresolved": 0}
    try:
        # SAVEPOINT, not a bare try/except: on failure this must roll back
        # ONLY this step's own writes, not the master/④/①/sub-file/SAP-
        # stock work already staged (uncommitted) earlier in this same
        # session — a plain session.rollback() here would discard ALL of
        # that too, since run() commits the whole session once at the end.
        with session.begin_nested():
            shipments = sap_shipments.parse_sap_shipments()
            if shipments:
                agg_rows = sap_shipments.aggregate_by_month(shipments)
                ship_stats = _ingest_sap_shipments(session, norm_index, agg_rows)
                report["sap_shipments"] = {
                    "path": str(shipments["path"]), "rows": len(agg_rows), **ship_stats,
                    "week_ending_dates": [d.isoformat() for d in shipments["week_ending_dates"]],
                }
                print(f"[SAP shipments] file={shipments['path'].name}  "
                      f"weeks={shipments['week_ending_dates'][0]}→{shipments['week_ending_dates'][-1]}  "
                      f"items={len(agg_rows)}  resolved={ship_stats['resolved']}  "
                      f"unresolved={ship_stats['unresolved']}")
            else:
                print("[SAP shipments] no 部材利用(出荷) sheet found — skipped, non-blocking for M1")
    except Exception as e:                          # noqa: BLE001
        report["sap_shipments"] = {"path": None, "rows": 0, "resolved": 0, "unresolved": 0,
                                   "error": repr(e)}
        print(f"[SAP shipments] FAILED, skipping non-blocking: {e!r}")

    # 6d. ZP21 組込 (build-incorporation) data → WeeklyActual — replaces
    # SAP shipments as the plan-vs-actual weekly source as of 2026-07-23
    # (see ingestion/sap_zp21.py docstring for why: procurement tracks
    # demand by build-incorporation timing, not PC-shipment timing).
    # Manually-placed weekly files (data/sap/組込/), same non-blocking
    # tier as the SAP stock history backfill above.
    zp21_weeks = sap_zp21.parse_all_zp21()
    report["sap_zp21"] = {"weeks": len(zp21_weeks), "resolved": {}, "total_items": {}}
    for week in zp21_weeks:
        agg: dict = {}
        matched = 0
        for row in week["rows"]:
            item = resolve_any(session, norm_index, row["code"], prefer_source="商品コード")
            if item is None and row["name_text"]:
                item = resolve_any(session, norm_index, row["name_text"], prefer_source="ONE綽名")
            if item is not None:
                matched += 1
            key = item.id if item is not None else ("unresolved", row["code"])
            e = agg.setdefault(key, {"item": item, "qty": 0.0})
            e["qty"] += row["qty"]
        for e in agg.values():
            item = e["item"]
            if item is not None:
                session.add(WeeklyActual(item_id=item.id, week_start=week["week_start"],
                                         qty=e["qty"], source="SAP"))
        report["sap_zp21"]["resolved"][str(week["week_start"])] = matched
        report["sap_zp21"]["total_items"][str(week["week_start"])] = len(agg)
        print(f"[SAP ZP21] {week['path'].name}  week={week['week_start']}→{week['week_end']}  "
              f"resolved_items={matched}/{len(agg)}")
    if not zp21_weeks:
        print("[SAP ZP21] no dated 組込 files found — skipped, non-blocking")

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

    # 8. source reconciliation — master vs sub-file disagreement, near-term
    # months only (see verify/reconciliation.py). Flags, doesn't resolve:
    # a disagreement can be a genuine planning-meeting revision in master
    # OR a formula error — needs a human look either way.
    from .verify import reconciliation as reconciliation_mod
    recon = reconciliation_mod.run(session)
    report["reconciliation"] = recon
    if recon["count"]:
        print(f"[reconciliation] {recon['count']} item-months where master and "
              f"the sub-file disagree by >{reconciliation_mod.MIN_RELATIVE_PCT:.0f}% "
              f"({recon['affected_items']} items) — needs human review, see "
              f"/reports/source-reconciliation or the 検証 workbook")
    else:
        print("[reconciliation] no significant master/sub-file disagreements "
              "in the near-term window")

    session.close()
    return report


if __name__ == "__main__":
    run()
