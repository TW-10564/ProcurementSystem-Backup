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
    session.close()
    return stats


def resolve(session, name: str) -> Item | None:
    """Resolve any source-system name to its canonical item."""
    row = (session.query(ItemAlias).filter(ItemAlias.alias == name).first())
    return row.item if row else None
