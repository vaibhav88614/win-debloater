"""Tests for the GitHub update-check helper."""

from __future__ import annotations

import io
import json

from app.core import updates


def test_parse_version_tolerates_v_and_suffix():
    assert updates.parse_version("v1.2.3") == (1, 2, 3)
    assert updates.parse_version("1.2.0-rc1") == (1, 2, 0)
    assert updates.parse_version("2.0") == (2, 0)
    assert updates.parse_version("") == (0,)


def test_is_newer():
    assert updates.is_newer("1.2.0", "1.1.9") is True
    assert updates.is_newer("v2.0.0", "1.9.9") is True
    assert updates.is_newer("1.0.0", "1.0.0") is False
    assert updates.is_newer("1.0.0", "1.0.1") is False


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def test_check_for_update_reports_newer(monkeypatch):
    payload = {"tag_name": "v9.9.9", "html_url": "https://example/releases/9.9.9"}

    def fake_urlopen(req, timeout=5):
        return _FakeResp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    info = updates.check_for_update(repo="x/y", current="1.0.0")
    assert info is not None
    assert info["latest"] == "9.9.9"
    assert info["newer"] is True
    assert info["url"].startswith("https://")


def test_check_for_update_not_newer(monkeypatch):
    payload = {"tag_name": "v1.0.0", "html_url": "u"}
    monkeypatch.setattr(
        updates.urllib.request,
        "urlopen",
        lambda req, timeout=5: _FakeResp(json.dumps(payload).encode("utf-8")),
    )
    info = updates.check_for_update(repo="x/y", current="1.0.0")
    assert info["newer"] is False


def test_check_for_update_returns_none_on_error(monkeypatch):
    import urllib.error

    def boom(req, timeout=5):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    assert updates.check_for_update(repo="x/y") is None


def test_check_for_update_returns_none_without_tag(monkeypatch):
    monkeypatch.setattr(
        updates.urllib.request,
        "urlopen",
        lambda req, timeout=5: _FakeResp(b"{}"),
    )
    assert updates.check_for_update(repo="x/y") is None
