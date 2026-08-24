"""Unit tests for ingestion/stock_policy.py's 在庫の持ち方 parser."""
import openpyxl
import pytest

from onefcst.ingestion import stock_policy


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    def _make(rows):
        path = tmp_path / "在庫の持ち方20260101.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["分類", "在庫日数(上限)"])
        for row in rows:
            ws.append(list(row))
        wb.save(path)
        monkeypatch.setattr(stock_policy, "STOCK_POLICY_GLOB", str(tmp_path / "在庫の持ち方*.xlsx"))
        return path
    return _make


def test_case_ge_gem_is_parsed_into_name_prefix_policy_not_skipped(policy_file):
    """FIX 2026-08-20: this label used to be thrown out entirely (SKIPPED_LABELS)
    because Item.subcategory can't identify GE/GEm — it now has a real number
    and must come back under name_prefix_policy, not skipped."""
    policy_file([("Case GE/GEm", "90日"), ("Case その他", "60日")])
    result = stock_policy.parse_stock_policy()
    assert result["name_prefix_policy"] == {("PCｹｰｽ", "GE"): 90.0}
    assert result["policy"] == {("PCｹｰｽ", None): 60.0}
    assert ("Case GE/GEm", "90日") not in result["skipped"]


def test_case_collabo_stays_skipped_since_it_has_no_real_number(policy_file):
    """個別相談 ("case-by-case negotiation") is not a day count — genuinely
    nothing to assign, unlike Case GE/GEm."""
    policy_file([("Case コラボ", "個別相談")])
    result = stock_policy.parse_stock_policy()
    assert result["name_prefix_policy"] == {}
    assert result["policy"] == {}
    assert ("Case コラボ", "個別相談") in result["skipped"]
