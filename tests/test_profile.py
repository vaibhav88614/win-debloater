"""Tests for profile capture/apply/diff."""

from __future__ import annotations

from types import SimpleNamespace

from app.core import profile


def _ok(error: str = ""):
    return SimpleNamespace(ok=True, error=error, stdout="")


def test_capture_profile(monkeypatch):
    svc = [
        SimpleNamespace(name="A", start_type="Manual"),
        SimpleNamespace(name="B", start_type="Auto"),
    ]
    tasks = [SimpleNamespace(full_path="\\X\\T", enabled=True)]
    pkgs = [SimpleNamespace(name="Pkg.One"), SimpleNamespace(name="Pkg.Two")]
    monkeypatch.setattr(profile.services, "list_services", lambda: svc)
    monkeypatch.setattr(profile.scheduled_tasks, "list_tasks", lambda: tasks)
    monkeypatch.setattr(profile.appx, "list_installed", lambda force=False: pkgs)

    p = profile.capture_profile()
    assert p["version"] == profile.PROFILE_VERSION
    assert p["services"] == {"A": "Manual", "B": "Auto"}
    assert p["tasks"] == {"\\X\\T": True}
    assert p["appx_present"] == ["Pkg.One", "Pkg.Two"]


def test_save_and_load_profile(tmp_path):
    prof = {
        "version": profile.PROFILE_VERSION,
        "services": {"A": "Manual"},
        "tasks": {},
        "appx_present": [],
    }
    path = tmp_path / "p.json"
    profile.save_profile(path, prof)
    loaded = profile.load_profile(path)
    assert loaded["services"] == {"A": "Manual"}


def test_load_profile_fills_defaults(tmp_path):
    path = tmp_path / "p.json"
    path.write_text('{"version": 1}', encoding="utf-8")
    loaded = profile.load_profile(path)
    assert loaded["services"] == {}
    assert loaded["tasks"] == {}
    assert loaded["appx_present"] == []


def test_apply_profile_changes_only_diffs(monkeypatch):
    current_services = [
        SimpleNamespace(name="A", start_type="Auto", is_protected=False),
        SimpleNamespace(name="B", start_type="Manual", is_protected=False),
        SimpleNamespace(name="P", start_type="Auto", is_protected=True),
    ]
    current_tasks = [
        SimpleNamespace(full_path="\\X\\T", enabled=True, path="\\X\\", name="T"),
    ]
    monkeypatch.setattr(profile.services, "list_services", lambda: current_services)
    monkeypatch.setattr(profile.scheduled_tasks, "list_tasks", lambda: current_tasks)

    calls = []
    monkeypatch.setattr(
        profile.services, "set_start_type", lambda n, t: (calls.append(("svc", n, t)), _ok())[1]
    )
    monkeypatch.setattr(
        profile.scheduled_tasks,
        "set_enabled",
        lambda p, n, e: (calls.append(("task", p, n, e)), _ok())[1],
    )

    prof = {
        "services": {"A": "Disabled", "B": "Manual", "P": "Disabled"},
        "tasks": {"\\X\\T": False},
    }
    report = profile.apply_profile(prof)
    # A changed (Auto->Disabled); B unchanged; P protected -> skipped.
    assert report.services_changed == 1
    assert report.tasks_changed == 1
    assert ("svc", "A", "Disabled") in calls
    assert ("task", "\\X\\", "T", False) in calls
    assert report.total_changed == 2


def test_apply_profile_records_errors(monkeypatch):
    current_services = [SimpleNamespace(name="A", start_type="Auto", is_protected=False)]
    monkeypatch.setattr(profile.services, "list_services", lambda: current_services)
    monkeypatch.setattr(profile.scheduled_tasks, "list_tasks", lambda: [])
    monkeypatch.setattr(
        profile.services, "set_start_type", lambda n, t: SimpleNamespace(ok=False, error="boom")
    )
    report = profile.apply_profile({"services": {"A": "Disabled"}, "tasks": {}})
    assert report.services_changed == 0
    assert report.errors and "boom" in report.errors[0]


def test_diff_profiles():
    old = {
        "services": {"A": "Auto", "B": "Manual"},
        "tasks": {"T1": True},
        "appx_present": ["X", "Y"],
    }
    new = {
        "services": {"A": "Disabled", "B": "Manual"},
        "tasks": {"T1": False, "T2": True},
        "appx_present": ["Y", "Z"],
    }
    d = profile.diff_profiles(old, new)
    assert d["services"] == {"A": ("Auto", "Disabled")}
    assert d["tasks"]["T1"] == (True, False)
    assert d["tasks"]["T2"] == (None, True)
    assert d["appx_removed"] == ["X"]
    assert d["appx_added"] == ["Z"]
    assert profile.has_changes(d) is True


def test_has_changes_false_for_identical():
    same = {"services": {"A": "Auto"}, "tasks": {}, "appx_present": ["X"]}
    assert profile.has_changes(profile.diff_profiles(same, same)) is False
