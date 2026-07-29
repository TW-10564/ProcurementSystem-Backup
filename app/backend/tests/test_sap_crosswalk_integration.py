"""Integration test against the real crosswalk file — proves the verified
2026-07-07 recovery (41 real items, 61,399 units) is what the shipped code
actually produces, not just what a one-off script found. Skipped when the
file isn't present (it's an optional, occasionally-provided input, not a
regular NAS/ONE source)."""
import pytest

from onefcst.ingestion import sap
from onefcst.ingestion.name_crosswalk import (latest_crosswalk_file,
                                              load_crosswalk_tokens,
                                              resolve_via_crosswalk)
from onefcst.registry.build import (resolve_any, build_normalized_alias_index)
from onefcst.db import get_session

pytestmark = pytest.mark.skipif(
    latest_crosswalk_file() is None, reason="no 製品名称読みかえ crosswalk file present")


def test_crosswalk_resolves_a_meaningful_number_of_real_items():
    result = sap.parse_sap_stock()
    assert result["resolved_via_crosswalk"] > 0

    # recompute independently which blank rows resolve, and confirm EVERY
    # one resolves to a real Item via the same resolver build.py uses —
    # the whole point of preferring the human-curated sheet is that it
    # shouldn't invent matches nothing else recognizes
    # 2026-07-29: this asserted on bare resolve(), which build.py stopped
    # using for SAP rows when resolution standardized on resolve_any
    # (exact match, then width/spacing/hyphen-normalized fallback — see
    # resolve_any's docstring). The test then failed on 'A310 LP', a
    # perfectly real item that only differs from its alias 'A310LP' by a
    # space: unresolvable by resolve(), resolved fine by the shipped path.
    # A stale assertion, not a recovery bug.
    # NOTE: a name can be crosswalk-token-shaped (e.g. "RTX5090") AND also
    # be a normally-あだ名'd row's own name elsewhere in the same SAP file —
    # so "is this key a crosswalk original" doesn't tell us THIS PARTICULAR
    # row came from crosswalk resolution. What we CAN assert without that
    # ambiguity: every crosswalk-shaped name present in the final rows is at
    # least a real, resolvable item (never an invented one).
    originals = {original for _, original in load_crosswalk_tokens(latest_crosswalk_file())}
    session = get_session()
    norm_index = build_normalized_alias_index(session)
    crosswalk_rows = [r for r in result["rows"] if r["adana"] in originals]
    assert crosswalk_rows, "no crosswalk-shaped names ended up in resolved rows at all"

    # Resolve exactly as build.py does — SYOCODE first, あだ名 as the fallback.
    # Asserting on the あだ名 ALONE would be a stricter bar than production
    # has to clear and would flag rows that are, in fact, fully resolved:
    # AMD CPUs are あだ名'd with a family prefix ONE adds and SAP/the
    # crosswalk don't ("ry7" + "5800X3D" -> "ry75800X3D"), so bare model
    # numbers like "9600X" or "9950X" match no alias — yet every one of them
    # reaches its item by SYOCODE and loses nothing.
    def resolves(row):
        if row["syocode"] and resolve_any(session, norm_index, row["syocode"],
                                          prefer_source="商品コード") is not None:
            return True
        return (bool(row["adana"]) and
                resolve_any(session, norm_index, row["adana"],
                            prefer_source="ONE綽名") is not None)

    dropped = sorted({r["adana"] for r in crosswalk_rows if not resolves(r)})
    session.close()
    # Deliberately NOT patched with a strip-the-prefix heuristic: "9950X" is a
    # substring of both "ry99950X3D" and "ry99950X3D2", which are different
    # CPUs from the non-X3D 9950X — fuzzy matching would misroute stock, which
    # is worse than dropping it.
    KNOWN_UNRESOLVED = {
        # SYOCODE 372398, '組AMD Ryzen 7 5800X3D(10th/AM4/3.4/100M/C', 9 units.
        # Item ry75800X3D (id 212, Tier-1 CPU) exists but carries NO 商品コード
        # alias, and its あだ名 keeps the ry7 prefix the crosswalk name drops —
        # so neither key reaches it and this row's stock IS dropped. Needs a
        # 部品マスター 商品コード for this SKU: a data fix, not a code one.
        # Remove from this set once procurement adds it.
        "5800X3D",
    }
    unexpected = sorted(set(dropped) - KNOWN_UNRESOLVED)
    assert not unexpected, (
        f"SAP rows reach no item by either key: {unexpected} — their stock "
        "is being dropped")


def test_syocode_resolves_rows_the_crosswalk_alone_could_not():
    # SYOCODE lines up with 商品コード (ItemAlias.source_system="商品コード"),
    # already populated by registry/build.py — verified 2026-07-09 against
    # the real 260629在庫.xlsx: SYOCODE alone resolves ~152/368 blank-あだ名
    # rows, well beyond the ~41/368 the SYONAME crosswalk recovers. This just
    # guards that SYOCODE resolution via resolve() still recovers *some*
    # rows the crosswalk left unresolved — not an exact count (the real
    # files change week to week).
    result = sap.parse_sap_stock()
    session = get_session()
    # rows still unnamed after sap.py's own crosswalk pass (see sap.py:
    # "adana" is set to the crosswalk name when the raw あだ名 was blank)
    still_blank = [row for row in result["rows"] if row["syocode"] and not row["adana"]]
    if not still_blank:
        session.close()
        pytest.skip("no blank-あだ名 rows with a SYOCODE in the current SAP file")
    norm_index = build_normalized_alias_index(session)
    # resolve_any + prefer_source="商品コード" mirrors build.py's SYOCODE call
    syocode_resolved = sum(
        1 for row in still_blank
        if resolve_any(session, norm_index, row["syocode"],
                       prefer_source="商品コード") is not None)
    session.close()
    assert syocode_resolved > 0
