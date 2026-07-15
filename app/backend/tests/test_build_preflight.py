"""Regression test — build.run() must never drop the live database when a
required NAS source is unreachable. Found live 2026-07-15 (twice, in the
same session): the DB got silently wiped by Base.metadata.drop_all()
before the missing-file error was ever raised, once when this agent ran
`onefcst.build` directly and once when the user clicked "Rebuild from
NAS" in an environment with no NAS mount — both times losing all data
rebuild_preserving.py exists to protect (which only helps a rebuild that
actually completes; it does nothing for one that dies before drop_all
even finishes). _check_required_sources() must catch this BEFORE any DB
write happens."""
import pytest

from onefcst.build import _check_required_sources, RequiredSourceUnreachable
from onefcst import config


def test_raises_without_touching_db_when_one_master_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ONE_MASTER", tmp_path / "does-not-exist.xlsm")
    monkeypatch.setattr("onefcst.build.ONE_MASTER", tmp_path / "does-not-exist.xlsm")
    with pytest.raises(RequiredSourceUnreachable) as exc:
        _check_required_sources()
    assert "does-not-exist.xlsm" in str(exc.value)


def test_error_lists_every_missing_source_not_just_the_first(tmp_path, monkeypatch):
    """When the whole NAS mount is down, every source is unreachable at
    once — the error should say so in full, not stop at the first hit,
    so a human fixing it sees the complete picture in one error."""
    fake_one = tmp_path / "ONE.xlsm"
    fake_note = tmp_path / "Note.xlsm"
    monkeypatch.setattr("onefcst.build.ONE_MASTER", fake_one)
    monkeypatch.setattr("onefcst.build.NOTE_ONE_MASTER", fake_note)
    monkeypatch.setattr("onefcst.build.planning_file",
                        lambda prefix: (_ for _ in ()).throw(
                            FileNotFoundError(f"no {prefix}* file")))
    with pytest.raises(RequiredSourceUnreachable) as exc:
        _check_required_sources()
    msg = str(exc.value)
    assert str(fake_one) in msg
    assert str(fake_note) in msg
    assert "①" in msg or "no ①" in msg
    assert "④" in msg or "no ④" in msg


def test_passes_silently_when_all_required_sources_exist(tmp_path, monkeypatch):
    one = tmp_path / "ONE.xlsm"
    note = tmp_path / "Note.xlsm"
    one.touch()
    note.touch()
    monkeypatch.setattr("onefcst.build.ONE_MASTER", one)
    monkeypatch.setattr("onefcst.build.NOTE_ONE_MASTER", note)
    monkeypatch.setattr("onefcst.build.planning_file", lambda prefix: tmp_path / f"{prefix}.xlsx")
    _check_required_sources()   # must not raise


def test_run_aborts_before_drop_all_when_sources_are_missing(tmp_path, monkeypatch):
    """The actual regression: run() itself must call the pre-flight check
    before Base.metadata.drop_all(), not just have the check function
    exist unused."""
    import onefcst.build as build_mod

    monkeypatch.setattr(build_mod, "ONE_MASTER", tmp_path / "missing.xlsm")

    called = {"drop_all": False}
    monkeypatch.setattr(build_mod.Base.metadata, "drop_all",
                        lambda *a, **k: called.__setitem__("drop_all", True))

    with pytest.raises(RequiredSourceUnreachable):
        build_mod.run()

    assert called["drop_all"] is False, "drop_all() ran despite a missing required source"
