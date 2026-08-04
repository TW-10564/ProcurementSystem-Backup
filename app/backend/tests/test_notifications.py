"""Tests for advisory/notifications.py's Japanese localization.

`AuditLog.what` stays plain English prose (unchanged — still what `classify`,
`_kind_filter` and the `q` search operate on). A write site MAY additionally
merge `notifications.event(code, **args)` into the `after` JSON it already
builds; `localize_what`/`_row` then render the SAME event in Japanese from
those args when `lang="ja"`, without touching the stored English text or
requiring a schema change. Rows without a recognised `_code` (written before
this existed, or from a call site that hasn't been migrated) just keep
showing the English `what` — not an error.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from onefcst.advisory import notifications as notif_svc
from onefcst.advisory import order_actions as order_svc
from onefcst.advisory import rules as rules_svc
from onefcst.api.main import app
from onefcst.models import AuditLog, Base, CompatRule, Item


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_localize_what_returns_english_unchanged_by_default():
    after = notif_svc.event("order_action.marked_ordered", item_name="RTX4070",
                            qty_display=200, date_display="2026-08-04")
    import json
    assert notif_svc.localize_what("order_action#1 marked ordered", json.dumps(after), "en") \
        == "order_action#1 marked ordered"


def test_localize_what_renders_japanese_from_the_event_code():
    import json
    after = notif_svc.event("order_action.marked_ordered", item_name="RTX4070",
                            qty_display=200, date_display="2026-08-04")
    ja = notif_svc.localize_what("order_action#1 marked ordered", json.dumps(after), "ja")
    assert "RTX4070" in ja
    assert "200" in ja
    assert "2026-08-04" in ja
    assert ja != "order_action#1 marked ordered"


def test_localize_what_falls_back_to_english_for_legacy_rows_with_no_code():
    """Rows written before this feature existed have no `_code` in `after`
    (or no `after` at all) — must degrade to the English sentence, not error."""
    assert notif_svc.localize_what("adjustment#1 recorded (sku)", None, "ja") \
        == "adjustment#1 recorded (sku)"
    assert notif_svc.localize_what("adjustment#1 recorded (sku)", "{}", "ja") \
        == "adjustment#1 recorded (sku)"
    assert notif_svc.localize_what("adjustment#1 recorded (sku)", "not json", "ja") \
        == "adjustment#1 recorded (sku)"


def test_localize_what_falls_back_to_english_on_a_missing_template_arg():
    """A future template change or a hand-crafted `after` that's missing an
    arg the template needs must not 500 the notifications feed."""
    import json
    after = {"_code": "order_action.marked_ordered", "_args": {"item_name": "RTX4070"}}
    assert notif_svc.localize_what("order_action#1 marked ordered", json.dumps(after), "ja") \
        == "order_action#1 marked ordered"


def test_kind_classification_is_unaffected_by_language(session):
    """classify() must always read the canonical English `what`, never the
    localized text, so `_kind_filter` (SQL-level) and the returned `kind`
    agree regardless of which language the caller asked to render in."""
    item = Item(canonical_name="RTX4070", category="VGA", kind="part")
    session.add(item); session.commit()
    order_svc.mark_ordered(session, item, qty_ordered=200, note="",
                           expected_arrival_date=date(2026, 8, 1), author="alice")

    en_row = notif_svc.recent_activity(session, lang="en")[0]
    ja_row = notif_svc.recent_activity(session, lang="ja")[0]
    assert en_row["kind"] == ja_row["kind"]
    assert en_row["what"] != ja_row["what"]
    assert "RTX4070" in ja_row["what"]


def test_order_action_lifecycle_is_fully_localized(session):
    item = Item(canonical_name="RTX4070", category="VGA", kind="part")
    session.add(item); session.commit()
    oa = order_svc.mark_ordered(session, item, qty_ordered=200, note="",
                                expected_arrival_date=date(2026, 8, 1), author="alice")
    order_svc.extend_expected_date(session, oa.id, date(2026, 8, 15), author="alice")
    order_svc.confirm_received(session, oa.id, author="alice")

    rows = notif_svc.recent_activity(session, lang="ja")
    assert len(rows) == 3
    for row in rows:
        assert "RTX4070" in row["what"]
    whats = {row["what"] for row in rows}
    assert any("入荷を確認" in w for w in whats)
    assert any("到着予定日を" in w for w in whats)
    assert any("発注を記録" in w for w in whats)


def test_compat_rule_localization_includes_both_part_names(session):
    cpu = Item(canonical_name="Ryzen 5 7500F", category="CPU", kind="part")
    mb = Item(canonical_name="B650M Pro", category="ﾏｻﾞｰﾎﾞｰﾄﾞ", kind="part")
    session.add_all([cpu, mb]); session.commit()
    rule = CompatRule(part_a_id=cpu.id, part_b_id=mb.id, relation="fits",
                      confidence=0.9, origin="ai_spec_heuristic", status="draft")
    session.add(rule); session.commit()

    rules_svc.confirm_rule(session, rule.id, author="bob")
    row = notif_svc.recent_activity(session, lang="ja")[0]
    assert "Ryzen 5 7500F" in row["what"]
    assert "B650M Pro" in row["what"]
    assert "承認" in row["what"]


def test_japanese_search_term_matches_english_stored_rows(session):
    """`what` is stored in English, but a user browsing the JA-rendered feed
    naturally searches in Japanese (including copy-pasting text straight off
    the screen). `q="却下"` must find a `dismiss`-worded English row."""
    item = Item(canonical_name="RTX4070", category="VGA", kind="part")
    session.add(item); session.commit()
    oa = order_svc.mark_ordered(session, item, qty_ordered=200, note="",
                                expected_arrival_date=date(2026, 8, 1), author="alice")
    order_svc.dismiss(session, oa.id, author="alice", reason="duplicate suggestion")

    result = notif_svc.query_activity(session, q="却下", lang="ja")
    assert result["total"] == 1
    assert "RTX4070" in result["items"][0]["what"]


def test_japanese_search_term_does_not_match_unrelated_rows(session):
    item = Item(canonical_name="RTX4070", category="VGA", kind="part")
    session.add(item); session.commit()
    order_svc.mark_ordered(session, item, qty_ordered=200, note="",
                           expected_arrival_date=date(2026, 8, 1), author="alice")

    result = notif_svc.query_activity(session, q="却下", lang="ja")
    assert result["total"] == 0


def test_item_name_appears_in_english_what_for_every_order_action_transition(session):
    """Regression: earlier, `dismiss`/`confirm_received`/`extend_expected_date`
    computed `item_name` for the Japanese event args but never put it in the
    English `what` sentence itself — so an English viewer (and English-side
    search) couldn't tell which item a bare "order_action#3 dismissed" row
    was even about."""
    item = Item(canonical_name="RTX4070", category="VGA", kind="part")
    session.add(item); session.commit()
    oa = order_svc.mark_ordered(session, item, qty_ordered=200, note="",
                                expected_arrival_date=date(2026, 8, 1), author="alice")
    order_svc.extend_expected_date(session, oa.id, date(2026, 8, 15), author="alice")
    order_svc.confirm_received(session, oa.id, author="alice")

    rows = session.query(AuditLog).all()
    assert len(rows) == 3
    assert all("RTX4070" in r.what for r in rows)


def test_notifications_endpoint_respects_lang_query_param(isolated_api_db):
    client = TestClient(app)
    item = Item(canonical_name="RTX4070", category="VGA", kind="part")
    isolated_api_db.add(item)
    isolated_api_db.commit()
    order_svc.mark_ordered(isolated_api_db, item, qty_ordered=200, note="",
                           expected_arrival_date=date(2026, 8, 1), author="alice")

    en = client.get("/notifications", params={"limit": 3}).json()["items"][0]
    ja = client.get("/notifications", params={"limit": 3, "lang": "ja"}).json()["items"][0]
    assert en["what"] != ja["what"]
    assert "RTX4070" in ja["what"]
    assert en["kind"] == ja["kind"]
