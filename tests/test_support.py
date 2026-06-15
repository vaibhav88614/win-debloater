"""Tests for the support-bundle builder."""

from __future__ import annotations

import zipfile
from types import SimpleNamespace

from app.core import support


def test_create_support_bundle(tmp_path, monkeypatch):
    # Redirect the log file to a temp file and stub diagnostics text.
    log = tmp_path / "app.log"
    log.write_text("a log line", encoding="utf-8")
    (tmp_path / "app.log.1").write_text("older log", encoding="utf-8")
    monkeypatch.setattr(support, "log_file_path", lambda: log)
    monkeypatch.setattr(support.diagnostics, "as_text", lambda: "DIAGNOSTICS BLOCK")

    actions = [
        SimpleNamespace(to_dict=lambda: {"kind": "x", "n": 1}),
        SimpleNamespace(to_dict=lambda: {"kind": "y", "n": 2}),
    ]
    dest = tmp_path / "bundle.zip"
    out = support.create_support_bundle(dest, actions)
    assert out == dest
    assert dest.exists()

    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert "diagnostics.txt" in names
        assert "history.json" in names
        assert "logs/app.log" in names
        assert "logs/app.log.1" in names
        assert zf.read("diagnostics.txt").decode("utf-8") == "DIAGNOSTICS BLOCK"
        import json

        hist = json.loads(zf.read("history.json"))
        assert len(hist) == 2


def test_create_support_bundle_truncates_history(tmp_path, monkeypatch):
    log = tmp_path / "app.log"
    log.write_text("x", encoding="utf-8")
    monkeypatch.setattr(support, "log_file_path", lambda: log)
    monkeypatch.setattr(support.diagnostics, "as_text", lambda: "D")
    actions = [SimpleNamespace(to_dict=lambda i=i: {"n": i}) for i in range(10)]
    dest = tmp_path / "b.zip"
    support.create_support_bundle(dest, actions, max_history=3)
    with zipfile.ZipFile(dest) as zf:
        import json

        hist = json.loads(zf.read("history.json"))
        assert len(hist) == 3
