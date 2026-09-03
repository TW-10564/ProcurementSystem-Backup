"""Gate C degrades instead of 500-ing when ①販売計画 is not on disk.

verify/backtest.py reads the 係数計算 history sheet LIVE from the ① planning
workbook (not from the DB), so on any checkout without the NAS planning files
`planning_file("①")` used to raise straight through the `/reports/backtest`
handler as an unhandled 500. A 5xx there trips the frontend's SERVER_ERROR_EVENT
and paints a misleading "cannot reach the API" screen for what is really just an
absent optional source — so `run()` now returns a marked "unavailable" payload,
mirroring build.py's ④-MISSING branch.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from onefcst import config
from onefcst.api import main as main_mod
from onefcst.verify import backtest


@pytest.fixture
def no_planning_dir(tmp_path, monkeypatch):
    """PLANNING_DIR points at an empty folder → find_planning_file('①') is None."""
    monkeypatch.setattr(config, "PLANNING_DIR", tmp_path)
    return tmp_path


def test_run_returns_unavailable_stub_when_source_absent(no_planning_dir):
    result = backtest.run()
    assert result["status"] == "unavailable"
    assert result["rows"] == []
    assert result["n_periods"] == 0
    assert result["winner"] is None
    assert "販売計画" in result["reason"]


def test_endpoint_is_200_not_500_when_source_absent(no_planning_dir):
    main_mod._report_cache.clear()          # never serve a cross-test cache hit
    try:
        client = TestClient(main_mod.app)
        r = client.get("/reports/backtest")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "unavailable"
        assert body["rows"] == []
    finally:
        main_mod._report_cache.clear()
