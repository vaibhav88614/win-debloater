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
        name="T", path=path, full_path=full,
        state="Ready", is_telemetry=True,
    )
    assert telemetry.safe is True


def test_known_telemetry_tasks_contains_appraiser():
    assert any("compatibility appraiser" in p for p in st.KNOWN_TELEMETRY_TASKS)
