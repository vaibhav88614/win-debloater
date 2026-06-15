"""Tests for diagnostics collection and rendering."""

from __future__ import annotations

from app.core import diagnostics


def test_collect_has_core_keys(monkeypatch):
    monkeypatch.setattr(diagnostics, "_defender_state", lambda: {})
    monkeypatch.setattr(diagnostics, "_disk_usage", lambda: (10.0, 100.0))
    info = diagnostics.collect()
    for key in (
        "app_version",
        "python",
        "platform",
        "os",
        "administrator",
        "disk_free_gb",
        "disk_total_gb",
        "log_file",
        "user_data_dir",
    ):
        assert key in info
    assert info["disk_free_gb"] == 10.0
    assert info["disk_total_gb"] == 100.0


def test_as_text_contains_fields():
    info = {
        "app_version": "9.9.9",
        "python": "3.13",
        "os": "TestOS",
        "machine": "x86",
        "administrator": True,
        "disk_free_gb": 5,
        "disk_total_gb": 100,
        "log_file": "L",
        "user_data_dir": "U",
    }
    text = diagnostics.as_text(info)
    assert "App version" in text
    assert "9.9.9" in text
    assert "TestOS" in text


def test_as_text_includes_defender_when_present():
    info = {
        "app_version": "1",
        "python": "3.13",
        "os": "x",
        "machine": "x",
        "administrator": False,
        "disk_free_gb": 1,
        "disk_total_gb": 2,
        "log_file": "L",
        "user_data_dir": "U",
        "restore_enabled": True,
        "defender": {
            "antivirus_enabled": True,
            "realtime_enabled": True,
            "service_enabled": True,
            "signatures_updated": "2026-01-01",
        },
    }
    text = diagnostics.as_text(info)
    assert "Defender" in text
    assert "System Restore" in text
