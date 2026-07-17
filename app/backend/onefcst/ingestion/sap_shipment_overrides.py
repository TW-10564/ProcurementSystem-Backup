"""Manual name-resolution overrides for SAP部材管理_詳細版*.xlsx shipment rows
whose raw label (読みかえ関数 output or fallback SAP code) doesn't exactly or
normalized-match any Item.canonical_name/ItemAlias — found 2026-07-17 while
investigating why several MSI CORELIQUID coolers showed 100% negative
planned-vs-actual variance (actual=0 despite a real monthly plan): those
items' real shipments were silently dropped into the "unresolved" bucket
instead of being counted, because the export's raw label uses a "v2"/白
suffix variant of the catalog's canonical name (e.g. SAP's own "v2" naming
sits between two hardware revisions the catalog doesn't distinguish).

Each entry here was verified against the actual item catalog before being
added (see the item's canonical_name it maps to) — this is deliberately
NOT a fuzzy/substring match like ingestion/name_crosswalk.py's resolver,
because a wrong guess here silently redirects real shipment quantity onto
the wrong SKU, which then feeds a wrong order-quantity suggestion (real
money). Ambiguous cases (e.g. "AK400 白" — catalog only has plain "AK400"
and a distinct "AK400-WH-ARGB", and it's not clear which one "白" refers
to; "大手裏剣" — catalog only has a "大手裏剣 1700" variant, raw label
doesn't specify 1700) were deliberately left OUT rather than guessed;
they still resolve to None and should be confirmed with procurement
before adding.

Raw labels are matched via the same width/case/spacing-insensitive
normalization registry/build.py's resolve_normalized() uses (so a
half-width vs full-width space, or a different letter case, doesn't
silently miss), applied to BOTH name_a (読みかえ関数 output) and
name_b (raw SAP code) — see build.py's _resolve_shipment_row order."""
from __future__ import annotations

from ..registry.build import _norm_for_crosswalk

# raw shipment-sheet label -> target Item.canonical_name (verified against
# the live catalog 2026-07-17)
_RAW_OVERRIDES: dict[str, str] = {
    "MAG CORELIQUID E360v2 黒": "MAG CORELIQUID E360 黒",
    "MAG CORELIQUID E240v2白/TW": "MAG CORELIQUID E240 WH/TW",
    "670LS FAN": "670LS",
    "虎徹3": "虎徹Ⅲ",
    "虎徹2": "虎徹Ⅱ",
    "L240 1700対応": "L240",
    # "R{n}-{model}" -> "ry{n}{model}" — same prefix convention every other
    # Ryzen CPU in the catalog already uses (e.g. "ry59600X"); catalog has
    # exactly one match for each, no ambiguity.
    "R5-5500GT": "ry55500GT",
    "9600X": "ry59600X",
}

SAP_SHIPMENT_NAME_OVERRIDES: dict[str, str] = {
    _norm_for_crosswalk(raw): target for raw, target in _RAW_OVERRIDES.items()
}


def resolve_shipment_override(name) -> str | None:
    """Returns the target Item.canonical_name to resolve against (via the
    normal resolve_any path), or None if this raw label has no verified
    override."""
    if not name:
        return None
    return SAP_SHIPMENT_NAME_OVERRIDES.get(_norm_for_crosswalk(name))
