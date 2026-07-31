"""Loading the permissions roster from the CSV the departments send.

The roster arrives as a pasted table, so the parser's job is to be strict about
the things that silently produce a broken permission (a typo'd sub-file owns
nothing; a duplicated address means two rows disagree about one person) and
forgiving about the things that do not matter (which word the department used
for the role, whether the separator was ; or 、).

The behaviour worth defending hardest is that a roster NEVER deletes. A
truncated paste must not lock out a department.
"""
from __future__ import annotations

import pytest

from onefcst import access_roster
from onefcst.api.permissions import ADMIN, PLANNING, PURCHASING


def write(tmp_path, body: str):
    p = tmp_path / "roster.csv"
    p.write_text("氏名,メールアドレス,部署,権限,担当パーツ\n" + body, encoding="utf-8")
    return p


def test_parses_the_real_shape_of_the_planning_list(tmp_path):
    path = write(tmp_path,
                 "筒井 愼平,s_tsutsui@twave.co.jp,企画部,管理者,\n"
                 "市村 久瑠美,k_ichimura@twave.co.jp,企画部,企画,\n")
    rows, problems = access_roster.parse(path)
    assert problems == []
    assert [r["role"] for r in rows] == [ADMIN, PLANNING]
    assert rows[0]["email"] == "s_tsutsui@twave.co.jp"


def test_role_words_are_accepted_in_either_language(tmp_path):
    """The departments write 管理者/購買/企画; nobody should be asked to re-send
    a file because they wrote 購買本部 instead of 購買."""
    path = write(tmp_path,
                 "A,a@twave.co.jp,購買本部,購買本部,MEM\n"
                 "B,b@twave.co.jp,x,admin,\n"
                 "C,c@twave.co.jp,x,planning,\n")
    rows, _ = access_roster.parse(path)
    assert [r["role"] for r in rows] == [PURCHASING, ADMIN, PLANNING]


def test_email_is_lowercased_to_match_the_token(tmp_path):
    path = write(tmp_path, "A,S_Tsutsui@TWave.co.JP,企画部,企画,\n")
    rows, _ = access_roster.parse(path)
    assert rows[0]["email"] == "s_tsutsui@twave.co.jp"


def test_scope_separators_are_all_accepted(tmp_path):
    """Pasted from Excel, the separator is whatever the author felt like.

    The comma case is the dangerous one: in a comma-separated file `MEM,SSD`
    splits across columns, and reading only the named column would grant MEM
    and silently drop SSD — the buyer could not touch half their parts.
    """
    for raw in ("MEM;SSD", "MEM,SSD", "MEM、SSD", "MEM/SSD"):
        path = write(tmp_path, f"A,a@twave.co.jp,購買本部,購買,{raw}\n")
        rows, _ = access_roster.parse(path)
        assert set(rows[0]["scopes"]) == {"MEM", "SSD"}, raw


def test_unknown_sub_file_is_rejected(tmp_path):
    """A typo'd key owns nothing, which is indistinguishable from a permissions
    bug once the buyer is refused."""
    path = write(tmp_path, "A,a@twave.co.jp,購買本部,購買,MEMORY\n")
    rows, problems = access_roster.parse(path)
    assert rows == []
    assert any("MEMORY" in p for p in problems)


def test_unknown_role_is_reported_and_the_row_dropped(tmp_path):
    path = write(tmp_path, "A,a@twave.co.jp,購買本部,部長,\n")
    rows, problems = access_roster.parse(path)
    assert rows == []
    assert any("unknown role" in p for p in problems)


def test_duplicate_address_is_reported_rather_than_last_one_wins(tmp_path):
    """Two rows for one person disagree about their access; silently taking
    the second would make the roster's meaning depend on row order."""
    path = write(tmp_path,
                 "A,a@twave.co.jp,購買本部,購買,MEM\n"
                 "A,a@twave.co.jp,購買本部,管理者,\n")
    rows, problems = access_roster.parse(path)
    assert len(rows) == 1
    assert any("already listed" in p for p in problems)


def test_purchasing_without_parts_is_flagged(tmp_path):
    """They would be able to change nothing — worth catching before the person
    reports the portal as broken."""
    path = write(tmp_path, "A,a@twave.co.jp,購買本部,購買,\n")
    rows, problems = access_roster.parse(path)
    assert any("担当パーツ" in p for p in problems)
    assert rows[0]["scopes"] == []          # still loadable, just powerless


def test_parts_on_a_planning_row_are_ignored_not_stored(tmp_path):
    """Planning's remit is by category, not by sub-file. Storing scopes would
    imply a restriction the permission code does not apply."""
    path = write(tmp_path, "A,a@twave.co.jp,企画部,企画,MEM\n")
    rows, problems = access_roster.parse(path)
    assert rows[0]["scopes"] == []
    assert any("ignored" in p for p in problems)


def test_blank_rows_from_a_pasted_table_are_skipped(tmp_path):
    path = write(tmp_path, "A,a@twave.co.jp,企画部,企画,\n,,,,\n,,,,\n")
    rows, problems = access_roster.parse(path)
    assert len(rows) == 1
    assert problems == []


def test_a_row_with_no_at_sign_is_rejected(tmp_path):
    path = write(tmp_path, "A,not-an-address,企画部,企画,\n")
    rows, problems = access_roster.parse(path)
    assert rows == []
    assert any("not an email" in p for p in problems)
