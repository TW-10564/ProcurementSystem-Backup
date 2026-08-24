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

import json

from sqlalchemy import func

from ..config import TIER1_CATEGORIES, ONE_DIR
from ..db import get_session
from ..models import Item, ItemAlias, Suggestion
from ..ingestion import one_master, note_plan, notepc_forecast, planning4, stock_policy
from ..ingestion import mb_procurement
from ..ingestion import no_reorder_flag
from ..ingestion import lead_time as lead_time_mod
from ..advisory.discontinuation_review import create_draft as _create_discontinuation_draft


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

    # Cross-system code lookup (found 2026-07-14): a 部品マスター row's OWN
    # name (row["adana"], e.g. "汎S 組ATX 650W 80+B") is very often a
    # different string from the SAME part's 部品予測 name (e.g. "B650"), so
    # by_adana.get(row["adana"]) alone misses it — even though row["code"]
    # (商品コード) is ALREADY registered as a 汎用コード alias of the real
    # item from step 1. Without this check, such a row silently created a
    # second, permanently-empty ghost Item under the 部品マスター spelling
    # (confirmed: 20 real PSU/CPU/memory pairs, e.g. "B650" vs its ghost
    # "汎S 組ATX 650W 80+B" — the ghost had zero stock/plan history forever,
    # while SAP stock and forecasts correctly stayed on the real item; but
    # the ghost's OWN commercial attributes below — moq/lead_time/suppliers —
    # were then silently lost, attached to an item nothing ever resolves to).
    # Built once from step 1/1b's aliases only, not live-updated against
    # master's own newly-created items — those duplicates are already
    # handled by the by_adana same-name-repeat path below.
    code_to_item: dict[str, int] = {alias: iid for (alias, _system), iid in alias_cache.items()}

    # Cross-category name collision (found 2026-07-14): row["adana"] can
    # coincidentally match an UNRELATED existing item's canonical_name from
    # a DIFFERENT category — e.g. "P1000" is a real GPU's own name in 部品予測,
    # but is ALSO ONE's own PSU shorthand ("Platinum 1000W") in a SEPARATE
    # 部品マスター row (code 194927 = "組ENH ATX-5010GB Rev3(ATX1000w,80+P,
    # JPcd)", an actual power supply whose own adana field happens to be the
    # identical string "P1000"). Silently reusing the GPU item here would
    # misattribute the PSU's own SAP stock/demand onto the GPU's numbers the
    # day this code ever appears in a SAP extract (confirmed dormant today —
    # 194927 isn't in the current export — but latent). Keyed by row["code"]
    # (not row["adana"], which is the ambiguous part) so repeated master rows
    # for the SAME miscategorized code land on the same disambiguated item.
    disambiguated_by_code: dict[str, Item] = {}

    for row in master_rows:
        item = by_adana.get(row["adana"])
        recovered_cat = bunrui_to_category.get(row["bunrui"])
        category_conflict = (item is not None and item.category and recovered_cat
                             and item.category != recovered_cat
                             and (item.category in TIER1_CATEGORIES
                                  or recovered_cat in TIER1_CATEGORIES))
        if category_conflict:
            # Before inventing a new item, check whether this code is
            # ALREADY a known alias of some OTHER (correctly-categorized)
            # item — found 2026-07-14: code 194927 was both a false name-
            # match onto the GPU "P1000" (the conflict this branch exists
            # for) AND already the real PSU "P1000W"'s own 汎用コード from
            # step 1. Re-splitting into yet another new item would recreate
            # the exact ghost-duplicate problem code_to_item exists to avoid.
            other_item_id = code_to_item.get(row["code"])
            if other_item_id is not None and other_item_id != item.id:
                item = session.get(Item, other_item_id)
                by_adana[row["adana"]] = item
            else:
                item = disambiguated_by_code.get(row["code"])
            if item is None:
                disambiguated_name = f"{row['adana']}#{row['code']}"
                item = Item(canonical_name=disambiguated_name, kind="part",
                           category=recovered_cat,
                           tier=1 if recovered_cat in TIER1_CATEGORIES else 2)
                session.add(item)
                session.flush()
                disambiguated_by_code[row["code"]] = item
                _add_alias(session, alias_cache, item.id, disambiguated_name, "ONE綽名", stats)
                stats["master_category_collision_disambiguated"] = (
                    stats.get("master_category_collision_disambiguated", 0) + 1)
        elif item is None and row["code"] in code_to_item:
            item = session.get(Item, code_to_item[row["code"]])
            by_adana[row["adana"]] = item
            _add_alias(session, alias_cache, item.id, row["adana"], "商品マスター名", stats)
            stats["master_code_matched_existing_item"] = (
                stats.get("master_code_matched_existing_item", 0) + 1)
        if item is None:
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
    # Verified 2026-07-09: 7 of 11 previously-unresolved rows here were pure
    # case/spacing variants of a real 綽名 ("Ry75700G" vs canonical
    # "ry75700G", "4000 SFF Ada" vs "4000SFFAda") that resolve_any's
    # normalization fallback already handles everywhere else — this was the
    # one call site still doing a bare exact-match dict lookup.
    norm_adana = {_norm_for_crosswalk(a): item for a, item in by_adana.items()}
    for row in planning4.parse_masta_aliases():
        item = by_adana.get(row["one_name"])
        if item is None:
            item = norm_adana.get(_norm_for_crosswalk(row["one_name"]))
        if item is None:
            stats["unresolved_plan4"] += 1
            continue
        _add_alias(session, alias_cache, item.id, row["plan_name"], "④企画名前", stats)
        stats["plan4_aliases"] += 1

    # -- 5. category-level max_stock_days ceiling (在庫の持ち方) --------------
    # FIX 2026-08-20: this used to fill Item.min_stock_days (a floor field)
    # with a CEILING value (在庫の持ち方's 在庫日数上限) whenever 部品マスター
    # left no per-item minimum — i.e. it silently relabeled a "don't exceed
    # N days" cap as "never go below N days," which stock_projection.py then
    # used as a reorder trigger, causing confirmed live over-ordering (see
    # stock_projection.suggest_order()'s docstring). This is a distinct
    # concept from min_stock_days, not a fallback for it, so every item with
    # a category match gets its ceiling here regardless of whether it also
    # has its own genuine min_stock_days floor. See ingestion/stock_policy.py
    # for the label crosswalk and which source rows are deliberately skipped.
    # FIX 2026-08-20: "Case GE/GEm" has a real 90-day ceiling but can't be
    # identified by category/subcategory (Item.subcategory is form-factor
    # only) — matched by Item.canonical_name PREFIX instead, checked BEFORE
    # the category/subcategory lookup so it isn't shadowed by the generic
    # "Case その他" bucket. See ingestion/stock_policy.py's docstring.
    policy_result = stock_policy.parse_stock_policy()
    policy = policy_result["policy"]
    name_prefix_policy = policy_result["name_prefix_policy"]
    filled = 0
    if policy or name_prefix_policy:
        for item in by_adana.values():
            if item.category is None:
                continue
            days = None
            for (cat, prefix), prefix_days in name_prefix_policy.items():
                if item.category == cat and item.canonical_name.startswith(prefix):
                    days = prefix_days
                    break
            if days is None:
                days = policy.get((item.category, item.subcategory))
            if days is None:
                days = policy.get((item.category, None))
            if days is not None:
                item.max_stock_days = days
                filled += 1
    stats["stock_policy_ceiling_filled"] = filled
    stats["stock_policy_source"] = policy_result["source_file"]
    stats["stock_policy_skipped"] = policy_result["skipped"]

    # -- 6. MB take-or-pay / exclusive-part liability (製品一覧) -------------
    # ingestion/mb_procurement.py's parser + chipset rollup already existed
    # and is used at REPORT time by verify/mismatch.py, but was never called
    # during build — so Item.exclusive_flag/takeorpay_n1/chipset stayed NULL
    # for every item despite the source data being live. Board-model
    # granularity is finer than our chipset-level registry items, so this
    # rolls up by chipset (see mb_procurement's own docstring for why) and
    # keeps the most severe takeorpay_n1 string seen for that chipset
    # (あり > 要交渉 > なし) rather than mismatch.py's report-time boolean
    # collapse, since Item.takeorpay_n1 is a string column.
    _TAKEORPAY_SEVERITY = {"あり": 2, "要交渉": 1, "なし": 0}
    liability_rows = mb_procurement.parse_mb_takeorpay(ONE_DIR)
    chipset_liab: dict[str, dict] = {}
    for r in liability_rows:
        if not r["chipset"]:
            continue
        agg = chipset_liab.setdefault(r["chipset"], {"exclusive": False, "takeorpay_n1": None})
        agg["exclusive"] = agg["exclusive"] or r["exclusive"]
        cand = r["takeorpay_n1"]
        cur = agg["takeorpay_n1"]
        if cand and (cur is None or _TAKEORPAY_SEVERITY.get(cand, -1) >
                     _TAKEORPAY_SEVERITY.get(cur, -1)):
            agg["takeorpay_n1"] = cand

    mb_liability_filled = 0
    for item in by_adana.values():
        if item.category != "ﾏｻﾞｰﾎﾞｰﾄﾞ":
            continue
        chipset = mb_procurement._find_chipset(item.canonical_name)
        if not chipset:
            continue
        info = chipset_liab.get(chipset)
        if info is None:
            continue
        item.chipset = chipset
        item.exclusive_flag = item.exclusive_flag or info["exclusive"]
        if info["takeorpay_n1"] is not None:
            item.takeorpay_n1 = info["takeorpay_n1"]
        mb_liability_filled += 1
    stats["mb_liability_chipsets"] = len(chipset_liab)
    stats["mb_liability_items_filled"] = mb_liability_filled

    # -- 7. "won't reorder" gray-cell marking (部品詳細, .xlsx categories only) --
    # Purchasing's own explicit "won't reorder" decision, read fresh from
    # the live cell color on every build (never a static/cached list — a
    # SKU un-marked gray in a later file simply stops being newly detected
    # on the next build, same as any other ingested field).
    #
    # Policy (2026-07-17, user instruction): the FIRST time a SKU is ever
    # seen gray-marked, it does NOT auto-flip eol_flag — it creates a draft
    # discontinuation Suggestion (advisory/discontinuation_review.py) for a
    # human to confirm or reject, the same "system suggests, human decides"
    # pattern already used for compat rules. eol_flag only gets set once
    # confirm_suggestion() runs. A SKU already marked_no_reorder=True from a
    # PRIOR build (already reviewed once, confirmed or still pending) does
    # not get a duplicate draft — only genuinely new detections do.
    # (The 15 SKUs already gray-marked as of 2026-07-17 were reviewed and
    # directly promoted to eol_flag=True that day as a one-time backfill —
    # not through this queue; see project memory.)
    # Only 6 of 17 categories are readable (.xlsx, not .xlsb) — items in the
    # other 11 simply never get this flag set, a tooling limitation, not a
    # claim those items are never marked.
    no_reorder_filled = 0
    discontinuation_drafts_created = 0
    existing_draft_item_ids = set()
    for s in session.query(Suggestion).filter(Suggestion.kind == "discontinuation").all():
        try:
            item_id = json.loads(s.payload).get("item_id")
        except (TypeError, ValueError):
            continue
        if item_id is not None:
            existing_draft_item_ids.add(item_id)
    for cat, skus in no_reorder_flag.find_all_no_reorder_skus().items():
        for sku in skus:
            item = by_adana.get(sku)
            if item is None:
                continue
            if item.marked_no_reorder:
                continue   # already seen gray in a prior build — no duplicate draft
            item.marked_no_reorder = True
            no_reorder_filled += 1
            if item.id not in existing_draft_item_ids and not item.eol_flag:
                _create_discontinuation_draft(session, item, category_file=cat)
                discontinuation_drafts_created += 1
    stats["no_reorder_items_filled"] = no_reorder_filled
    stats["discontinuation_drafts_created"] = discontinuation_drafts_created

    # -- 8. real observed lead time (購買計画 PO history, delivered only) -----
    # See ingestion/lead_time.py's docstring — this can diverge sharply from
    # 部品マスター's static lead_time_days; both are kept (never overwrites
    # lead_time_days), see forecast_views.effective_lead_time for how a
    # caller picks between them.
    observed_filled = 0
    for sku, (median_days, n) in lead_time_mod.compute_observed_lead_times().items():
        item = by_adana.get(sku)
        if item is None:
            continue
        item.observed_lead_time_days = median_days
        item.observed_lead_time_samples = n
        observed_filled += 1
    stats["observed_lead_time_items_filled"] = observed_filled

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


def resolve_via_source(session, name: str, source_system: str) -> Item | None:
    """Resolve `name` only against aliases from ONE specific source_system —
    for callers whose own context should outrank resolve()'s generic
    lowest-alias-id tie-break.

    Real case (2026-07-09): "B650" is both a PSU's own 綽名 (item 899,
    ONE綽名, added in step 1 — lower id) and a motherboard's officially
    curated planning nickname (item 575 "B650 TW", ④企画名前, added in step
    4). resolve() silently picked the PSU for every ①販売計画 row named
    "ASR B650" (ASR-prefix-stripped to "B650" by planning1.py), routing a
    Tier-1 motherboard's sales-plan data to an unrelated Tier-2 accessory.
    ④企画名前 exists SPECIFICALLY to bridge planning-origin names to the
    right item, so a planning-sourced name should check it first rather
    than falling through to resolve()'s source-agnostic pick."""
    row = (session.query(ItemAlias).filter(ItemAlias.alias == name,
                                           ItemAlias.source_system == source_system)
          .order_by(ItemAlias.id.asc()).first())
    return row.item if row else None


def resolve(session, name: str, prefer_source: str | None = None) -> Item | None:
    """Resolve any source-system name to its canonical item.

    prefer_source: when the caller's own context already knows which alias
    system `name` should match against (e.g. SAP SYOCODE -> '商品コード',
    a 部品詳細 sheet's SKU column -> 'ONE綽名'), check that source FIRST.
    Found 2026-07-21: 6 more cross-system alias collisions exist beyond the
    documented B650 case (see build_registry's cross_system_alias_conflicts),
    each a variant SKU (Samsung/X3D/color suffix) sharing a numeric code
    with its base part under a DIFFERENT source_system — resolve_any()'s
    unguarded call into this function was silently misrouting real SAP
    stock rows to the wrong item for every one of them, the exact same bug
    class resolve_via_source was built for, just not propagated past its
    one original call site. Falls through to the source-agnostic tie-break
    below when the preferred source has no match, so callers that pass a
    prefer_source never resolve to LESS than they would have before.

    Deterministic tie-break (FIX_PLAN C3): when the same alias string is
    claimed by items from two different source systems (not blocked by the
    DB's unique constraint, which is per (alias, source_system)), ordering
    by ItemAlias.id picks the FIRST one ever recorded, consistently across
    runs — rather than arbitrary DB storage order. This does not resolve
    the underlying ambiguity (see build_registry's
    cross_system_alias_conflicts for that), it only makes the outcome
    stable and reproducible instead of silently flipping between rebuilds."""
    if prefer_source is not None:
        preferred = (session.query(ItemAlias)
                    .filter(ItemAlias.alias == name, ItemAlias.source_system == prefer_source)
                    .order_by(ItemAlias.id.asc()).first())
        if preferred is not None:
            return preferred.item
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


class AliasIndex(dict):
    """The normalized-alias index (a plain {normalized_alias: item} dict, so
    every existing caller and resolve_normalized() are unaffected) carrying
    two EXACT-match indexes alongside it:

      .exact_by_source  {(alias, source_system): item}
      .exact_any        {alias: item}

    Both are populated in the same single pass over ItemAlias with the same
    lowest-id-wins rule resolve() applies via .order_by(ItemAlias.id.asc()),
    so resolve_any() can answer from memory instead of issuing SQL per name.

    Why this exists (2026-07-28): resolve() ran 1-2 queries per call and
    then touched row.item, a lazy relationship load — a THIRD query. At
    80,494 resolve_any() calls per build that was ~160k round-trips and
    ~12% of total build time, all to re-read a 9k-row table that is already
    fully loaded here."""

    __slots__ = ("exact_by_source", "exact_any")

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.exact_by_source: dict[tuple[str, str], Item] = {}
        self.exact_any: dict[str, Item] = {}


def build_normalized_alias_index(session) -> AliasIndex:
    """{normalized_alias: item} — first (lowest ItemAlias.id, matching
    resolve()'s own deterministic tie-break) wins a collision. Built once
    per caller rather than per-row: normalizing 9k+ aliases per blank-あだ名
    row would be wasteful.

    Also carries exact-match indexes for resolve_any — see AliasIndex."""
    index = AliasIndex()
    # joinedload: without it, row.item below fires one lazy SELECT per alias
    # row (9k queries just to build the index).
    from sqlalchemy.orm import joinedload
    rows = (session.query(ItemAlias).options(joinedload(ItemAlias.item))
            .order_by(ItemAlias.id.asc()).all())
    for row in rows:
        key = _norm_for_crosswalk(row.alias)
        if key and key not in index:
            index[key] = row.item
        # same lowest-id-wins tie-break as resolve(): first writer keeps it
        src_key = (row.alias, row.source_system)
        if src_key not in index.exact_by_source:
            index.exact_by_source[src_key] = row.item
        if row.alias not in index.exact_any:
            index.exact_any[row.alias] = row.item
    return index


def resolve_normalized(index: dict[str, Item], name: str) -> Item | None:
    """Fallback for resolve() using a prebuilt normalized-alias index (see
    build_normalized_alias_index) — width/spacing/hyphen-insensitive."""
    key = _norm_for_crosswalk(name)
    return index.get(key) if key else None


def resolve_any(session, norm_index: dict[str, Item], name: str,
                prefer_source: str | None = None) -> Item | None:
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
    would be a full alias-table scan per row.

    prefer_source: passed straight through to resolve() — see its
    docstring. Pass this whenever the caller's own field already carries a
    known identity type (SAP SYOCODE, a 部品詳細 sheet's 綽名 column, etc.)."""
    # Fast path: answer the exact-match step from the in-memory index built
    # by build_normalized_alias_index (see AliasIndex). Identical semantics
    # to resolve() — preferred source first, then source-agnostic, both
    # lowest-ItemAlias.id-wins. Falls back to the querying resolve() when a
    # caller passed a plain dict (older/ad-hoc call sites) so behaviour is
    # unchanged for them.
    exact_by_source = getattr(norm_index, "exact_by_source", None)
    if exact_by_source is not None:
        item = None
        if prefer_source is not None:
            item = exact_by_source.get((name, prefer_source))
        if item is None:
            item = norm_index.exact_any.get(name)
    else:
        item = resolve(session, name, prefer_source=prefer_source)
    return item if item is not None else resolve_normalized(norm_index, name)
