"""Unified item registry builder (M1).

Merges, in priority order:
  1. ONE計画 部品予測  — the authoritative 綽名 universe + category/EOL
  2. ONE計画 部品マスター — commercial attributes (MOQ/LT/min-stock/suppliers)
     + 商品コード aliases (many codes → one 綽名)
  3. ノート在庫使用計画 マスタ — notebook models w/ lifecycle + MOQ条件
  4. ④ ﾏｽﾀ — planning-name aliases (企画名前 → 綽名)

Every alias records its source system. Conflicts are counted, not guessed.
"""
from __future__ import annotations

from sqlalchemy import func

from ..config import TIER1_CATEGORIES
from ..db import get_session
from ..models import Item, ItemAlias
from ..ingestion import one_master, note_plan, notepc_forecast, planning4


def _add_alias(session, cache: dict, item_id: int, alias: str, system: str,
               stats: dict) -> None:
    key = (alias, system)
    if key in cache:
        if cache[key] != item_id:
            stats["alias_conflicts"] += 1
            stats["conflict_rows"].append(
                {"alias": alias, "system": system,
                 "kept_item_id": cache[key], "dropped_item_id": item_id})
        return
    session.add(ItemAlias(item_id=item_id, alias=alias, source_system=system))
    cache[key] = item_id


def build_registry() -> dict:
    stats = {"items": 0, "aliases": 0, "alias_conflicts": 0,
             "master_attr_hits": 0, "note_models": 0, "plan4_aliases": 0,
             "unresolved_plan4": 0, "conflict_rows": []}
    session = get_session()
    alias_cache: dict[tuple, int] = {}
    by_adana: dict[str, Item] = {}

    # -- 1. 綽名 universe from 部品予測 ------------------------------------
    fc = one_master.parse_parts_forecast()
    for row in fc["items"]:
        if row["adana"] in by_adana:
            continue
        cat = row["category"]
        item = Item(canonical_name=row["adana"], kind="part",
                    category=cat, subcategory=row["subcategory"],
                    tier=1 if cat in TIER1_CATEGORIES else 2,
                    eol_flag=row["eol"])
        session.add(item)
        session.flush()
        by_adana[row["adana"]] = item
        _add_alias(session, alias_cache, item.id, row["adana"], "ONE綽名", stats)
        if row["generic_code"]:
            _add_alias(session, alias_cache, item.id, row["generic_code"],
                       "汎用コード", stats)

    # -- 1b. NotePC 綽名 universe from NoteONE計画's OWN 部品予測 ------------
    # Found 2026-07-07: this sheet (2020-02→2028-12, 250 parts) existed the
    # whole time but nothing ever read it — every NotePC part had zero
    # forecast coverage, not from missing data, just an unwired source.
    notepc_fc = notepc_forecast.parse_notepc_forecast()
    for row in notepc_fc["items"]:
        if row["adana"] in by_adana:
            continue
        item = Item(canonical_name=row["adana"], kind="part",
                   category=row["category"], subcategory=row["subcategory"],
                   tier=2, eol_flag=row["eol"])
        session.add(item)
        session.flush()
        by_adana[row["adana"]] = item
        _add_alias(session, alias_cache, item.id, row["adana"], "ONE綽名", stats)
        if row["generic_code"]:
            _add_alias(session, alias_cache, item.id, row["generic_code"],
                       "汎用コード", stats)
        if row["model_no"]:
            _add_alias(session, alias_cache, item.id, row["model_no"], "製品型番", stats)
    stats["notepc_forecast_items"] = len(notepc_fc["items"])

    # -- 2. commercial attributes + 商品コード from 部品マスター -------------
    master_rows = one_master.parse_parts_master()

    # 部品マスター's own "bunrui" is a numeric category CODE (e.g. "79"), not
    # a name — but items that ALSO appear in 部品予測 (step 1) already have a
    # real category name AND that same code, so a majority vote per code
    # recovers what it means. Confirmed 2026-07-03/07: without this, ~1,397
    # real items that exist ONLY in 部品マスター (never in 部品予測 — mostly
    # generic accessories/consumables) get category=NULL forever, which is
    # exactly why they had no route to any forecast, derived or otherwise.
    # Some codes resolve to a Tier-1 category (e.g. 79 -> PCｹｰｽ), so tier
    # must be re-derived here too, not hardcoded to 2.
    bunrui_votes: dict[str, dict[str, int]] = {}
    for row in master_rows:
        item = by_adana.get(row["adana"])
        if item is not None and item.category and row["bunrui"]:
            votes = bunrui_votes.setdefault(row["bunrui"], {})
            votes[item.category] = votes.get(item.category, 0) + 1
    bunrui_to_category = {code: max(votes, key=votes.get)
                          for code, votes in bunrui_votes.items()}
    stats["bunrui_categories_recovered"] = len(bunrui_to_category)

    for row in master_rows:
        item = by_adana.get(row["adana"])
        if item is None:
            recovered_cat = bunrui_to_category.get(row["bunrui"])
            item = Item(canonical_name=row["adana"], kind="part",
                       category=recovered_cat,
                       tier=1 if recovered_cat in TIER1_CATEGORIES else 2)
            session.add(item)
            session.flush()
            by_adana[row["adana"]] = item
            _add_alias(session, alias_cache, item.id, row["adana"], "ONE綽名", stats)
        # first non-null wins (master has duplicate code rows)
        if item.moq is None:
            item.moq = row["moq"]
            item.lead_time_days = row["lead_time_days"]
            item.min_stock_days = row["min_stock_days"]
            item.supplier_a = row["supplier_a"]
            item.supplier_b = row["supplier_b"]
            item.supplier_c = row["supplier_c"]
            stats["master_attr_hits"] += 1
        _add_alias(session, alias_cache, item.id, row["code"], "商品コード", stats)

    # -- 3. notebook models (lifecycle master) ------------------------------
    for row in note_plan.parse_note_master():
        name = row["adana"] or row["model_no"]
        if not name:
            continue
        item = by_adana.get(name)
        if item is None:
            item = Item(canonical_name=name, kind="pc_model", category="NotePC",
                        tier=1, lifecycle=row["lifecycle"],
                        release_date=row["release_date"], eol_date=row["eol_date"])
            session.add(item)
            session.flush()
            by_adana[name] = item
        else:
            item.lifecycle = item.lifecycle or row["lifecycle"]
            item.release_date = item.release_date or row["release_date"]
            item.eol_date = item.eol_date or row["eol_date"]
        stats["note_models"] += 1
        for alias, system in ((row["adana"], "ノート綽名"),
                              (row["model_no"], "製品型番"),
                              (row["code"], "商品コード"),
                              (row["bare_name"], "ベア名称")):
            if alias:
                _add_alias(session, alias_cache, item.id, alias, system, stats)
    for row in note_plan.parse_note_moq():
        item = by_adana.get(row["adana"])
        if item is not None and item.moq is None:
            item.moq = row["moq"]

    # -- 4. planning-name aliases from ④ﾏｽﾀ --------------------------------
    for row in planning4.parse_masta_aliases():
        item = by_adana.get(row["one_name"])
        if item is None:
            stats["unresolved_plan4"] += 1
            continue
        _add_alias(session, alias_cache, item.id, row["plan_name"], "④企画名前", stats)
        stats["plan4_aliases"] += 1

    session.commit()
    stats["items"] = session.query(func.count(Item.id)).scalar()
    stats["aliases"] = session.query(func.count(ItemAlias.id)).scalar()

    # Cross-system alias collisions (FIX_PLAN C3): the same alias string
    # claimed by items from >1 source_system. _add_alias's conflict counter
    # only catches same-alias-SAME-source_system collisions (matching the
    # DB's own unique constraint); this is the wider check that surfaces the
    # case resolve() below can silently mis-resolve.
    dup_rows = (session.query(ItemAlias.alias,
                              func.count(func.distinct(ItemAlias.item_id)))
               .group_by(ItemAlias.alias)
               .having(func.count(func.distinct(ItemAlias.item_id)) > 1).all())
    stats["cross_system_alias_conflicts"] = len(dup_rows)
    stats["cross_system_conflict_aliases"] = [alias for alias, _ in dup_rows][:50]

    session.close()
    return stats


def resolve(session, name: str) -> Item | None:
    """Resolve any source-system name to its canonical item.

    Deterministic tie-break (FIX_PLAN C3): when the same alias string is
    claimed by items from two different source systems (not blocked by the
    DB's unique constraint, which is per (alias, source_system)), ordering
    by ItemAlias.id picks the FIRST one ever recorded, consistently across
    runs — rather than arbitrary DB storage order. This does not resolve
    the underlying ambiguity (see build_registry's
    cross_system_alias_conflicts for that), it only makes the outcome
    stable and reproducible instead of silently flipping between rebuilds."""
    row = (session.query(ItemAlias).filter(ItemAlias.alias == name)
          .order_by(ItemAlias.id.asc()).first())
    return row.item if row else None


def _norm_for_crosswalk(s) -> str:
    """Fullwidth→halfwidth, case-fold, strip spaces/hyphens — mirrors the
    proven norm() in consolidation/stock_paste.py (duplicated rather than
    imported: that module pulls in an unrelated legacy consolidation DB at
    import time). FIX_PLAN M3: resolve()'s exact-string match leaves a blank-
    あだ名 SAP row unmatched even after the crosswalk correctly identifies
    the item, when the two names differ only by width/spacing/hyphenation
    (e.g. SAP's fullwidth 'Ｂ450Ｍ' vs the ONE alias 'B450M')."""
    if s in (None, ""):
        return ""
    text = str(s).translate(str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
        "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
        "０１２３４５６７８９",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789",
    ))
    text = text.upper()
    for ch in "　 -_+()／/":
        text = text.replace(ch, "")
    return text.strip()


def build_normalized_alias_index(session) -> dict[str, Item]:
    """{normalized_alias: item} — first (lowest ItemAlias.id, matching
    resolve()'s own deterministic tie-break) wins a collision. Built once
    per caller rather than per-row: normalizing 9k+ aliases per blank-あだ名
    row would be wasteful."""
    index: dict[str, Item] = {}
    rows = (session.query(ItemAlias).order_by(ItemAlias.id.asc()).all())
    for row in rows:
        key = _norm_for_crosswalk(row.alias)
        if key and key not in index:
            index[key] = row.item
    return index


def resolve_normalized(index: dict[str, Item], name: str) -> Item | None:
    """Fallback for resolve() using a prebuilt normalized-alias index (see
    build_normalized_alias_index) — width/spacing/hyphen-insensitive."""
    key = _norm_for_crosswalk(name)
    return index.get(key) if key else None


def resolve_any(session, norm_index: dict[str, Item], name: str) -> Item | None:
    """The standard resolution path for every ingestion source: exact
    resolve() first (preserves its deterministic tie-break), normalized
    (width/spacing/hyphen/case-insensitive) fallback second.

    Standardized 2026-07-08 after three ingestion paths (SAP, ①販売計画,
    and an ad-hoc cross-check against a teammate's spreadsheet) each grew
    their own copy of "try exact, then normalized" — a real cross-system
    name difference (e.g. a CPU recorded as 'Ry53400G' in one file and
    'ry53400G' in another) isn't unique to any one source, so the fallback
    belongs in one shared place every ingestion path calls, not
    reimplemented per-caller. norm_index should be built ONCE per build
    run via build_normalized_alias_index (after build_registry() commits
    all aliases) and threaded through every call — rebuilding it per row
    would be a full alias-table scan per row."""
    item = resolve(session, name)
    return item if item is not None else resolve_normalized(norm_index, name)
