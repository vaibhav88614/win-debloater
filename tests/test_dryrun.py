"""Tests for the dry-run toggle."""

from __future__ import annotations

import pytest

from app.core import actionlog, appx, dryrun, processes, restore, scheduled_tasks, services


@pytest.fixture(autouse=True)
def _reset_dryrun():
    dryrun.set_enabled(False)
    yield
    dryrun.set_enabled(False)


def test_dryrun_set_and_get():
    assert not dryrun.is_enabled()
    dryrun.set_enabled(True)
    assert dryrun.is_enabled()


def test_dry_result_is_synthetic_success():
    res = dryrun.dry_result("would do thing")
    assert res.ok
    assert res.returncode == 0
    assert "dry-run" in res.stdout
    assert "would do thing" in res.stdout


def test_services_stop_returns_synthetic_when_dry():
    dryrun.set_enabled(True)
    res = services.stop_service("Spooler")
    assert res.ok
    assert "would stop service 'Spooler'" in res.stdout


def test_services_start_returns_synthetic_when_dry():
    dryrun.set_enabled(True)
    res = services.start_service("Spooler")
    assert res.ok
    assert "would start service 'Spooler'" in res.stdout


def test_services_set_start_type_returns_synthetic_when_dry():
    dryrun.set_enabled(True)
    res = services.set_start_type("Spooler", "Disabled")
    assert res.ok
    assert "Disabled" in res.stdout


def test_tasks_set_enabled_returns_synthetic_when_dry():
    dryrun.set_enabled(True)
    res = scheduled_tasks.set_enabled("\\Microsoft\\Foo\\", "Bar", False)
    assert res.ok
    assert "would disable" in res.stdout


def test_appx_remove_package_returns_synthetic_when_dry():
    dryrun.set_enabled(True)
    pkg = appx.AppxPackage(name="Microsoft.BingNews", full_name="Microsoft.BingNews_1.0_x64")
    res = appx.remove_package(pkg)
    assert res.ok
    assert "Microsoft.BingNews" in res.stdout


def test_restore_create_point_returns_synthetic_when_dry():
    dryrun.set_enabled(True)
    res = restore.create_restore_point("test-checkpoint")
    assert res.ok
    assert "test-checkpoint" in res.stdout


def test_processes_kill_returns_synthetic_when_dry():
    dryrun.set_enabled(True)
    ok, msg = processes.kill_process(99999)
    assert ok
    assert "dry-run" in msg
    ok, msg = processes.suspend_process(99999)
    assert ok
    assert "dry-run" in msg
    ok, msg = processes.resume_process(99999)
    assert ok
    assert "dry-run" in msg


def test_action_log_marks_dryrun_entries(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.actionlog.user_data_dir", lambda: tmp_path)
    dryrun.set_enabled(True)
    log = actionlog.ActionLog()
    a = log.add(
        actionlog.Action(
            kind=actionlog.KIND_SERVICE_STATE,
            target="Spooler",
            summary="Stopped service 'Spooler'",
            success=True,
            undoable=True,
            undo_data={"name": "Spooler"},
        )
    )
    assert a.undoable is False
    assert a.summary.startswith("(dry-run)")
