"""Procurement team's own maintained name crosswalk (製品名称読みかえ sheet) —
used to resolve blank-あだ名 SAP rows. Chosen 2026-07-07 over a regex/pattern
heuristic specifically because it's human-curated: the team already solved
this exact cross-system naming problem by hand (実績Excel名称 / ONE名称 →
their own display name), so trusting their answer is safer than re-deriving
a guess — a wrong guess here would feed a wrong quantity into an order
suggestion (real money), not just leave a gap.

Optional input: absence is expected and non-blocking. The source file
(SAP部材管理_詳細版*.xlsx, an internal procurement tool, not a NAS/ONE
source) may not always be provided — everything degrades gracefully to
"no crosswalk resolution this run" when it's missing.
"""
from __future__ import annotations

import glob
import unicodedata
from pathlib import Path

from ..config import NAME_CROSSWALK_GLOB
from .excel import read_xlsx_rows

SHEET_NAME = "製品名称読みかえ"
MIN_TOKEN_LEN = 4   # shorter tokens (e.g. "5070" inside "5070TI") false-match too easily


def latest_crosswalk_file() -> Path | None:
    hits = sorted(glob.glob(NAME_CROSSWALK_GLOB))
    return Path(hits[-1]) if hits else None


def _norm(s) -> str | None:
    if s in (None, ""):
        return None
    return unicodedata.normalize("NFKC", str(s)).strip().upper()


def load_crosswalk_tokens(path: Path) -> list[tuple[str, str]]:
    """(original, normalized) pairs from the 実績Excel名称/ONE名称 columns
    (NOT the 3rd 'display name' column — that's for the team's own Excel,
    not a join key), longest-normalized-first so resolve_via_crosswalk()
    prefers the most specific match (e.g. 'B550M' over 'B550').

    The ORIGINAL casing/width is kept because that's what must exactly
    match Item.canonical_name / ItemAlias.alias (resolve() does a plain
    string ==, no normalization) — confirmed 2026-07-07: an item stored as
    "B850 LiveMixer" does NOT resolve from an upper-cased "B850 LIVEMIXER".
    `normalized` exists only to make the SYONAME substring search
    case/width-insensitive; it is never used as the resolved name."""
    rows = read_xlsx_rows(path, SHEET_NAME, max_col=2, required=False)
    if rows is None:
        return []
    pairs = {}
    for row in rows[1:]:                              # row 0 is the header
        for v in row:
            if v in (None, ""):
                continue
            n = _norm(v)
            if n:
                pairs[n] = str(v).strip()          # last write wins; fine, values agree
    return sorted(pairs.items(), key=lambda kv: len(kv[0]), reverse=True)


def resolve_via_crosswalk(syoname, tokens: list[tuple[str, str]]) -> str | None:
    """Longest matching token wins. A shorter token is very often a real
    substring of a more specific one (e.g. '5070' inside '5070TI', 'B860'
    inside 'B860M LIVEMIXER') rather than a genuine alternative reading, so
    tie-breaking toward specificity is the safe default. tokens must already
    be sorted longest-normalized-first (load_crosswalk_tokens does this).
    Returns the ORIGINAL-cased name, not the normalized matching form."""
    up = _norm(syoname)
    if not up:
        return None
    for normalized, original in tokens:
        if len(normalized) >= MIN_TOKEN_LEN and normalized in up:
            return original
    return None
