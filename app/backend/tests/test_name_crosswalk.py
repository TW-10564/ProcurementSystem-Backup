"""Unit tests for the name-crosswalk resolver — pure logic, no file I/O.
Fixture tokens mirror real cases found 2026-07-07 (B550 vs B550M, 5070 vs
5070TI) that motivated the longest-match rule, and the exact-casing bug
(an item stored as "B850 LiveMixer" must be returned with that casing, not
an upper-cased "B850 LIVEMIXER" — resolve() does a plain string ==)."""
import pytest

from onefcst.ingestion.name_crosswalk import _norm, resolve_via_crosswalk

# (normalized, original) — original casing deliberately mixed, like real data
_ORIGINALS = ["B550M", "B550", "B860M LiveMixer", "B860M", "B860",
             "5070Ti", "5070", "A620AM", "X870E"]
TOKENS = sorted(((_norm(o), o) for o in _ORIGINALS), key=lambda kv: len(kv[0]), reverse=True)


def test_exact_and_prefix_match():
    assert resolve_via_crosswalk("組ASRock A620AM TW BK(A620A AM5)", TOKENS) == "A620AM"


def test_longest_token_wins_over_shorter_substring():
    # B550 is a real substring of B550M — must not stop at the shorter one
    assert resolve_via_crosswalk("組ASRock B550M Pro4(B550 AM4 DDR4 mATX)", TOKENS) == "B550M"
    assert resolve_via_crosswalk("組ASRock B860M LiveMixer WiFi(B860)", TOKENS) == "B860M LiveMixer"
    assert resolve_via_crosswalk("組UNW X6AR57TK(5070TI GPU)", TOKENS) == "5070Ti"


def test_returns_original_casing_not_normalized_form():
    # the resolve() this feeds does an exact ItemAlias.alias == name match —
    # returning the upper-cased matching form would silently fail to link
    assert resolve_via_crosswalk("組ASRock B860M LiveMixer WiFi", TOKENS) == "B860M LiveMixer"
    assert "B860M LIVEMIXER" != resolve_via_crosswalk("組ASRock B860M LiveMixer WiFi", TOKENS)


def test_no_match_returns_none():
    assert resolve_via_crosswalk("組ASRock Z890 Taichi(Z890 1851)", TOKENS) is None
    assert resolve_via_crosswalk(None, TOKENS) is None
    assert resolve_via_crosswalk("", TOKENS) is None


def test_short_tokens_below_min_length_are_ignored():
    # a 3-char-or-shorter token must never match, however tempting the
    # substring — this is the guard against false positives on short codes
    short_tokens = [(_norm("B55"), "B55")] + TOKENS
    assert resolve_via_crosswalk("組 something with B55 inside", short_tokens) is None


def test_normalization_is_case_and_width_insensitive():
    # full-width/half-width and case shouldn't matter for MATCHING, even
    # though the returned value keeps its original casing
    assert resolve_via_crosswalk("組asrock a620am tw", TOKENS) == "A620AM"
