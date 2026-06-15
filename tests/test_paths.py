"""Path resolution helpers."""

from __future__ import annotations

import os
from pathlib import Path

from app.core import paths


def test_base_dir_points_at_project_root():
    base = paths.base_dir()
    assert (base / "app" / "main.py").is_file()


def test_resource_path_resolves_bundled_files():
    catalog = paths.resource_path("app", "core", "data", "bloatware.json")
    qss = paths.resource_path("app", "resources", "style.qss")
    assert catalog.is_file()
    assert qss.is_file()


def test_user_data_dir_is_writable_and_under_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    udir = paths.user_data_dir()
    assert udir.exists()
    assert udir.name == "WinDebloater"
    assert Path(os.environ["LOCALAPPDATA"]) in udir.parents
    # Writable: round-trip a small file.
    probe = udir / "probe.txt"
    probe.write_text("ok", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "ok"
