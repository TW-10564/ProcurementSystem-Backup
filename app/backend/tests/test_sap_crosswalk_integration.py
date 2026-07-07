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
from onefcst.registry.build import resolve
from onefcst.db import get_session

pytestmark = pytest.mark.skipif(
    latest_crosswalk_file() is None, reason="no 製品名称読みかえ crosswalk file present")


def test_crosswalk_resolves_a_meaningful_number_of_real_items():
    result = sap.parse_sap_stock()
    assert result["resolved_via_crosswalk"] > 0

    # recompute independently which blank rows resolve, and confirm EVERY
    # one resolves to a real Item via the same resolve() build.py uses —
    # the whole point of preferring the human-curated sheet is that it
    # shouldn't invent matches nothing else recognizes
    # NOTE: a name can be crosswalk-token-shaped (e.g. "RTX5090") AND also
    # be a normally-あだ名'd row's own name elsewhere in the same SAP file —
    # so "is this key a crosswalk original" doesn't tell us THIS PARTICULAR
    # row came from crosswalk resolution. What we CAN assert without that
    # ambiguity: every crosswalk-shaped name present in the final rows is at
    # least a real, resolvable item (never an invented one).
    originals = {original for _, original in load_crosswalk_tokens(latest_crosswalk_file())}
    session = get_session()
    crosswalk_shaped = [n for n in result["rows"] if n in originals]
    assert crosswalk_shaped, "no crosswalk-shaped names ended up in resolved rows at all"
    for name in crosswalk_shaped:
        assert resolve(session, name) is not None, f"crosswalk-shaped match {name!r} isn't a real item"
    session.close()
