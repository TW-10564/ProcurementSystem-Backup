"""Guard: the calamine fast path must agree with openpyxl, cell for cell.

ingestion/excel.py reads VALUES through python-calamine because openpyxl
materialises the whole style table first and the ONE workbooks carry ~1M
named styles (that alone was ~61% of a full build). calamine is a different
reader with different native types, so "faster" is only acceptable while
"identical" holds.

This exists because the swap nearly shipped a silent data-loss bug
(2026-07-28): calamine returns every number as f64, so VGA's numeric 綽名
(5050/5060/5070/5080/5090 — real SKU nicknames) came back as 5090.0 and
stringified to "5090.0", matching nothing in the alias registry. 綽名 is the
join key for the whole system, so every numerically-named SKU would have
been silently orphaned — no exception, no warning, just missing forecasts.
_cal_value's int-normalisation fixes that, and these tests keep it fixed
through future openpyxl/calamine upgrades.

Two layers:
  * contract tests for _cal_value — pure, no I/O, always run
  * real-file parity for the .xlsx sub-files — needs the NAS cache, so it
    skips cleanly when absent. Only VGA (the numeric-綽名 case that actually
    broke) runs by default; set ONEFCST_FULL_EXCEL_PARITY=1 for all of them,
    which is worth doing after any upgrade of either library.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time
from pathlib import Path

import pytest

from onefcst.ingestion import excel as ex
from onefcst.ingestion import subfiles
from onefcst.ingestion.subfile_config import BASE as SUBFILE_BASE, FILES as SUBFILE_CFG


# ── contract of the value normaliser (no file I/O) ───────────────────────

def _norm(v):
    return ex._cal_value(v, date)


def test_empty_string_becomes_none():
    """openpyxl reports an empty cell as None; read_dict_rows and every
    `if v is None` check downstream depend on that, so "" must not survive."""
    assert _norm("") is None


def test_whole_float_becomes_int_the_numeric_adana_case():
    """THE regression this module exists for: str(5090.0) == '5090.0' would
    never match the alias '5090'."""
    assert _norm(5090.0) == 5090
    assert isinstance(_norm(5090.0), int)
    assert str(_norm(5090.0)) == "5090"


def test_fractional_float_is_left_alone():
    """Plan values are genuinely fractional — rounding them would corrupt
    real quantities, which is a worse fault than the one being fixed."""
    assert _norm(88.21280828066963) == pytest.approx(88.21280828066963)
    assert isinstance(_norm(88.21280828066963), float)


def test_date_is_widened_to_datetime():
    """openpyxl yields datetime for date cells; the month-bucketing code
    calls .date()/.month on these."""
    assert _norm(date(2026, 8, 1)) == datetime(2026, 8, 1)


def test_datetime_and_other_types_pass_through_untouched():
    dt = datetime(2026, 8, 1, 13, 30)
    assert _norm(dt) == dt
    assert _norm("RTX5090") == "RTX5090"
    assert _norm(None) is None
    assert _norm(True) is True
    assert _norm(time(9, 30)) == time(9, 30)


def test_bool_is_not_mangled_by_the_int_rule():
    """bool is a subclass of int in Python — a True that silently became 1
    (or vice versa) would be a quiet type change in any flag column."""
    assert _norm(True) is True
    assert isinstance(_norm(True), bool)


# ── real-file parity against openpyxl ────────────────────────────────────

_XLSX_CATS = [c for c, cfg in SUBFILE_CFG.items() if cfg["fmt"] == "xlsx"]
_FULL = os.environ.get("ONEFCST_FULL_EXCEL_PARITY") == "1"
# VGA carries the numeric 綽名 that broke; it is the highest-value single case.
_CATS = _XLSX_CATS if _FULL else [c for c in _XLSX_CATS if c == "VGA"]


def _source_path(cat: str) -> Path:
    cfg = SUBFILE_CFG[cat]
    return Path(cfg.get("_abspath") or (SUBFILE_BASE / cfg["file"]))


@pytest.mark.parametrize("cat", _CATS)
def test_parsed_output_identical_to_openpyxl(cat, monkeypatch):
    """Compare the PARSER'S OUTPUT, not raw cell values: the bug that got
    through raw-value review was a str() difference that only became visible
    once 綽名 was used as a key."""
    if not _source_path(cat).exists():
        pytest.skip(f"NAS cache missing: {_source_path(cat).name}")

    via_calamine = subfiles.parse_subfile_series(cat)
    # returning None from _calamine_sheet is exactly how the production code
    # falls back, so this exercises the real openpyxl path
    monkeypatch.setattr(ex, "_calamine_sheet", lambda *a, **k: None)
    via_openpyxl = subfiles.parse_subfile_series(cat)

    dumped = lambda o: json.dumps(o, default=str, sort_keys=True)
    assert dumped(via_openpyxl) == dumped(via_calamine), (
        f"{cat}: calamine and openpyxl disagree — the fast path is no longer "
        f"a pure optimisation"
    )


@pytest.mark.parametrize("cat", _CATS)
def test_adana_join_keys_are_string_identical(cat, monkeypatch):
    """Narrow assertion on the join key itself, so a failure names the real
    problem instead of pointing at a whole-structure diff."""
    if not _source_path(cat).exists():
        pytest.skip(f"NAS cache missing: {_source_path(cat).name}")

    cal_names = {r["adana"] for r in subfiles.parse_subfile_series(cat)["series"]}
    monkeypatch.setattr(ex, "_calamine_sheet", lambda *a, **k: None)
    op_names = {r["adana"] for r in subfiles.parse_subfile_series(cat)["series"]}

    assert cal_names == op_names, (
        f"{cat}: 綽名 join keys differ. Only in calamine: "
        f"{sorted(cal_names - op_names)[:5]} | only in openpyxl: "
        f"{sorted(op_names - cal_names)[:5]}"
    )
    assert not any(n.endswith(".0") for n in cal_names), (
        f"{cat}: numeric 綽名 leaked through as float-formatted strings "
        f"({sorted(n for n in cal_names if n.endswith('.0'))[:5]}) — these "
        f"match nothing in the alias registry"
    )
