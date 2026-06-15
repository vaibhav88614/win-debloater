"""Scheduled task helpers."""

from __future__ import annotations

from app.core import scheduled_tasks as st


def test_normalize_state_maps_integers_to_strings():
    assert st._normalize_state("1") == "Disabled"
    assert st._normalize_state("3") == "Ready"
    assert st._normalize_state("4") == "Running"
    # Unknown values pass through unchanged.
    assert st._normalize_state("Ready") == "Ready"
    assert st._normalize_state("") == ""


def test_task_info_enabled_and_safe_flags():
    path = "\\path\\"
    full = "\\path\\T"
    disabled = st.TaskInfo(name="T", path=path, full_path=full, state="Disabled")
    assert disabled.enabled is False
    assert disabled.safe is False

    enabled = st.TaskInfo(name="T", path=path, full_path=full, state="Ready")
    assert enabled.enabled is True

    telemetry = st.TaskInfo(
        name="T",
        path=path,
        full_path=full,
        state="Ready",
        is_telemetry=True,
    )
    assert telemetry.safe is True


def test_known_telemetry_tasks_contains_appraiser():
    assert any("compatibility appraiser" in p for p in st.KNOWN_TELEMETRY_TASKS)


def test_set_enabled_quotes_path_and_name(monkeypatch):
    captured = {}
    from app.core import powershell as ps

    def fake_run(script, *, timeout=60):
        captured["script"] = script
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    st.set_enabled(r"\Microsoft\Windows\It's\Task\\", "It's a Task", enabled=False)

    # Both path and name are single-quoted with apostrophes doubled.
    assert "'\\Microsoft\\Windows\\It''s\\Task\\\\'" in captured["script"]
    assert "'It''s a Task'" in captured["script"]
    assert "Disable-ScheduledTask" in captured["script"]


def test_set_enabled_uses_enable_verb_when_true(monkeypatch):
    captured = {}
    from app.core import powershell as ps

    monkeypatch.setattr(
        ps,
        "run",
        lambda script, *, timeout=60: (
            captured.update(script=script),
            ps.PSResult(ok=True, returncode=0),
        )[1],
    )
    st.set_enabled(r"\Microsoft\Windows\X\\", "T", enabled=True)
    assert "Enable-ScheduledTask" in captured["script"]


# ----------------------------- list_tasks parser ---------------------------

from types import SimpleNamespace


def _ps_items(items):
    return SimpleNamespace(ok=True, items=items)


def test_list_tasks_parses_typical_rows(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        st.ps,
        "run_json",
        lambda *a, **kw: _ps_items(
            [
                {
                    "TaskName": "Microsoft Compatibility Appraiser",
                    "TaskPath": "\\Microsoft\\Windows\\Application Experience\\",
                    "State": "Ready",
                    "Description": "Telemetry collector.",
                    "Author": "Microsoft",
                },
                {
                    "TaskName": "MyTask",
                    "TaskPath": "\\",
                    "State": 4,
                    "Description": "",
                    "Author": "user",
                },
            ]
        ),
    )
    out = st.list_tasks()
    by_name = {t.name: t for t in out}
    assert set(by_name) == {"Microsoft Compatibility Appraiser", "MyTask"}
    appraiser = by_name["Microsoft Compatibility Appraiser"]
    assert appraiser.is_telemetry is True
    # int state should be normalized to a known string.
    assert by_name["MyTask"].state == "Running"


def test_list_tasks_skips_empty_name(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        st.ps,
        "run_json",
        lambda *a, **kw: _ps_items(
            [
                {
                    "TaskName": "",
                    "TaskPath": "\\",
                    "State": "Ready",
                    "Description": "",
                    "Author": "",
                },
                {
                    "TaskName": "Keep",
                    "TaskPath": "\\",
                    "State": "Ready",
                    "Description": "",
                    "Author": "",
                },
            ]
        ),
    )
    assert [t.name for t in st.list_tasks()] == ["Keep"]


def test_list_tasks_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(st.ps, "run_json", lambda *a, **kw: SimpleNamespace(ok=False, items=[]))
    assert st.list_tasks() == []


def test_list_tasks_returns_empty_on_non_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert st.list_tasks() == []
