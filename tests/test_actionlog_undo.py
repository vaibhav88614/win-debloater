"""Happy-path undo tests for every supported action kind.

We patch the real core modules so we only validate that ``perform_undo``
dispatches correctly and returns the expected ``(ok, message)``.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core import actionlog
from app.core.actionlog import (
    KIND_APPX_REMOVE,
    KIND_PROCESS_SUSPEND,
    KIND_SERVICE_STARTTYPE,
    KIND_SERVICE_STATE,
    KIND_TASK_TOGGLE,
    Action,
)


def _ps_ok(stdout: str = "ok"):
    return SimpleNamespace(ok=True, returncode=0, stdout=stdout, stderr="", error=None)


def test_perform_undo_appx_calls_restore_package(monkeypatch):
    from app.core import appx

    calls = []

    def _restore(pkg):
        calls.append(("restore", pkg.name, pkg.full_name))
        return _ps_ok("restored")

    monkeypatch.setattr(appx, "restore_package", _restore)

    a = Action(
        kind=KIND_APPX_REMOVE,
        target="Foo",
        summary="Removed",
        undoable=True,
        undo_data={"name": "Foo", "full_name": "Foo_1.0"},
    )
    ok, msg = actionlog.perform_undo(a)
    assert ok
    assert "restored" in msg
    assert calls == [("restore", "Foo", "Foo_1.0")]


def test_perform_undo_service_starttype_calls_set_start_type(monkeypatch):
    from app.core import services

    calls = []

    def _set(name, start_type):
        calls.append((name, start_type))
        return _ps_ok()

    monkeypatch.setattr(services, "set_start_type", _set)

    a = Action(
        kind=KIND_SERVICE_STARTTYPE,
        target="Spooler",
        summary="",
        undoable=True,
        undo_data={"name": "Spooler", "previous_start_type": "Auto"},
    )
    ok, msg = actionlog.perform_undo(a)
    assert ok
    assert calls == [("Spooler", "Automatic")]
    assert "Spooler" in msg


def test_perform_undo_service_state_restarts_when_was_running(monkeypatch):
    from app.core import services

    calls = []

    monkeypatch.setattr(
        services, "start_service", lambda n: (calls.append(("start", n)), _ps_ok())[1]
    )
    monkeypatch.setattr(
        services, "stop_service", lambda n: (calls.append(("stop", n)), _ps_ok())[1]
    )

    a = Action(
        kind=KIND_SERVICE_STATE,
        target="Spooler",
        summary="",
        undoable=True,
        undo_data={"name": "Spooler", "was_running": True},
    )
    ok, _ = actionlog.perform_undo(a)
    assert ok
    assert calls == [("start", "Spooler")]


def test_perform_undo_service_state_stops_when_was_stopped(monkeypatch):
    from app.core import services

    calls = []

    monkeypatch.setattr(
        services, "start_service", lambda n: (calls.append(("start", n)), _ps_ok())[1]
    )
    monkeypatch.setattr(
        services, "stop_service", lambda n: (calls.append(("stop", n)), _ps_ok())[1]
    )

    a = Action(
        kind=KIND_SERVICE_STATE,
        target="Spooler",
        summary="",
        undoable=True,
        undo_data={"name": "Spooler", "was_running": False},
    )
    ok, _ = actionlog.perform_undo(a)
    assert ok
    assert calls == [("stop", "Spooler")]


def test_perform_undo_task_toggle_restores_previous_state(monkeypatch):
    from app.core import scheduled_tasks

    calls = []

    def _set(path, name, enabled):
        calls.append((path, name, enabled))
        return _ps_ok()

    monkeypatch.setattr(scheduled_tasks, "set_enabled", _set)

    a = Action(
        kind=KIND_TASK_TOGGLE,
        target="Foo",
        summary="",
        undoable=True,
        undo_data={"path": "\\Microsoft\\X\\", "name": "Foo", "was_enabled": True},
    )
    ok, msg = actionlog.perform_undo(a)
    assert ok
    assert calls == [("\\Microsoft\\X\\", "Foo", True)]
    assert "enabled" in msg


def test_perform_undo_process_suspend_calls_resume(monkeypatch):
    from app.core import processes

    calls = []

    monkeypatch.setattr(
        processes, "resume_process", lambda pid: (calls.append(pid), (True, f"resumed {pid}"))[1]
    )

    a = Action(
        kind=KIND_PROCESS_SUSPEND,
        target="proc",
        summary="",
        undoable=True,
        undo_data={"pid": 4242},
    )
    ok, msg = actionlog.perform_undo(a)
    assert ok
    assert calls == [4242]
    assert "4242" in msg


def test_perform_undo_unknown_kind_returns_failure():
    a = Action(kind="something_else", target="x", summary="", undoable=True)
    ok, msg = actionlog.perform_undo(a)
    assert not ok
    assert "no undo handler" in msg.lower()
