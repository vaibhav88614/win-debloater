"""Action history persistence and undo dispatcher."""

from __future__ import annotations

from pathlib import Path

from app.core import actionlog
from app.core.actionlog import Action, ActionLog


def _fresh_log(monkeypatch, tmp_path):
    """Build an ActionLog backed by ``tmp_path`` only."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return ActionLog()


def test_action_log_persists_across_instances(monkeypatch, tmp_path):
    log = _fresh_log(monkeypatch, tmp_path)
    log.add(
        Action(
            kind="appx_remove",
            target="Foo",
            summary="Removed Foo",
            undoable=True,
            undo_data={"name": "Foo"},
        )
    )
    log.add(Action(kind="appx_remove", target="Bar", summary="Removed Bar"))

    # Reload from disk via a new instance.
    log2 = ActionLog()
    items = log2.all()
    assert len(items) == 2
    # ``all`` returns newest first.
    assert items[0].target == "Bar"
    assert items[1].target == "Foo"


def test_action_log_undoable_filter(monkeypatch, tmp_path):
    log = _fresh_log(monkeypatch, tmp_path)
    log.add(Action(kind="appx_remove", target="A", summary="", undoable=True))
    log.add(Action(kind="appx_remove", target="B", summary="", undoable=False))
    log.add(Action(kind="appx_remove", target="C", summary="", undoable=True, success=False))

    undoable = log.undoable()
    targets = [a.target for a in undoable]
    assert targets == ["A"]


def test_action_log_mark_undone_persists(monkeypatch, tmp_path):
    log = _fresh_log(monkeypatch, tmp_path)
    a = log.add(Action(kind="appx_remove", target="A", summary="", undoable=True))
    log.mark_undone(a)

    log2 = ActionLog()
    assert log2.all()[0].undone is True
    assert log2.undoable() == []


def test_action_log_clear_wipes_history(monkeypatch, tmp_path):
    log = _fresh_log(monkeypatch, tmp_path)
    log.add(Action(kind="appx_remove", target="A", summary=""))
    log.clear()
    assert log.all() == []
    assert ActionLog().all() == []


def test_action_log_recovers_from_corrupt_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    target = Path(tmp_path) / "WinDebloater"
    target.mkdir(parents=True, exist_ok=True)
    (target / "action_history.json").write_text("{ not valid json", encoding="utf-8")

    log = ActionLog()
    assert log.all() == []
    # And the log is still usable after a corrupt load.
    log.add(Action(kind="appx_remove", target="A", summary=""))
    assert len(log.all()) == 1


def test_action_to_dict_roundtrip():
    a = Action(
        kind="appx_remove",
        target="X",
        summary="s",
        undoable=True,
        undo_data={"name": "X"},
        success=True,
    )
    b = Action.from_dict(a.to_dict())
    assert b.kind == a.kind
    assert b.target == a.target
    assert b.undoable is True
    assert b.undo_data == {"name": "X"}


def test_normalize_start_type_maps_known_values():
    assert actionlog._normalize_start_type("Auto") == "Automatic"
    assert actionlog._normalize_start_type("Automatic") == "Automatic"
    assert actionlog._normalize_start_type("Boot") == "Automatic"
    assert actionlog._normalize_start_type("Disabled") == "Disabled"
    assert actionlog._normalize_start_type("Manual") == "Manual"
    assert actionlog._normalize_start_type("AutomaticDelayedStart") == "AutomaticDelayedStart"
    assert actionlog._normalize_start_type("garbage") == "Manual"


def test_perform_undo_refuses_already_undone():
    a = Action(kind="appx_remove", target="X", summary="", undoable=True, undone=True)
    ok, msg = actionlog.perform_undo(a)
    assert not ok
    assert "cannot be undone" in msg.lower()


def test_perform_undo_refuses_non_undoable():
    a = Action(kind="restore_point", target="System", summary="", undoable=False)
    ok, msg = actionlog.perform_undo(a)
    assert not ok


def test_save_is_atomic_on_failure(monkeypatch, tmp_path):
    """A crash mid-save must preserve the existing valid file."""
    log = _fresh_log(monkeypatch, tmp_path)
    # First, save a valid entry so the on-disk file has known good content.
    a = log.add(Action(kind="appx_remove", target="Original", summary="kept", undoable=True))
    original_text = log.path.read_text(encoding="utf-8")
    assert "Original" in original_text

    # Make json.dump raise after opening the temp file.
    import json as _json

    original_dump = _json.dump

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(_json, "dump", boom)

    log._actions.append(Action(kind="appx_remove", target="Doomed", summary=""))
    log.save()  # must not raise

    # On-disk file is unchanged.
    assert log.path.read_text(encoding="utf-8") == original_text
    # And no leftover .tmp file.
    leftover = log.path.with_suffix(log.path.suffix + ".tmp")
    assert not leftover.exists()

    # Restore json.dump and confirm subsequent saves succeed normally.
    monkeypatch.setattr(_json, "dump", original_dump)
    log.save()
    assert "Doomed" in log.path.read_text(encoding="utf-8")
