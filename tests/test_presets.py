"""Tests for the preset loader."""

from __future__ import annotations

import json

from app.core import appx, presets


def _make_pkg(name: str, *, category: str = "", safe: bool = True) -> appx.AppxPackage:
    return appx.AppxPackage(name=name, full_name=f"{name}_1.0_x64", category=category, safe=safe)


def test_load_presets_returns_objects():
    out = presets.load_presets()
    assert out, "bundled presets file should have entries"
    assert all(isinstance(p, presets.Preset) for p in out)
    assert all(p.id and p.name for p in out)


def test_apply_preset_matches_by_category():
    p = presets.Preset(id="x", name="xbox", categories=["xbox"])
    pkgs = [
        _make_pkg("Microsoft.XboxApp", category="Xbox"),
        _make_pkg("Microsoft.BingNews", category="News & Info"),
        _make_pkg("Microsoft.XboxGamingOverlay", category="Xbox"),
    ]
    out = presets.apply_preset(p, pkgs)
    assert {x.name for x in out} == {"Microsoft.XboxApp", "Microsoft.XboxGamingOverlay"}


def test_apply_preset_matches_by_explicit_id():
    p = presets.Preset(id="x", name="custom", ids=["microsoft.bingnews"])
    pkgs = [
        _make_pkg("Microsoft.XboxApp", category="Xbox"),
        _make_pkg("Microsoft.BingNews", category="News & Info"),
    ]
    out = presets.apply_preset(p, pkgs)
    assert len(out) == 1
    assert out[0].name == "Microsoft.BingNews"


def test_apply_preset_safe_only_filters_unsafe():
    p = presets.Preset(id="x", name="safe", categories=["xbox"], safe_only=True)
    pkgs = [
        _make_pkg("Microsoft.XboxApp", category="Xbox", safe=True),
        _make_pkg("Microsoft.XboxIdentityProvider", category="Xbox", safe=False),
    ]
    out = presets.apply_preset(p, pkgs)
    assert [x.name for x in out] == ["Microsoft.XboxApp"]


def test_load_presets_invalid_file(tmp_path, monkeypatch):
    bad = tmp_path / "presets.json"
    bad.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(presets, "resource_path", lambda *a, **kw: bad)
    assert presets.load_presets() == []


def test_load_presets_skips_invalid_entries(tmp_path, monkeypatch):
    good = tmp_path / "presets.json"
    good.write_text(
        json.dumps(
            {
                "version": presets.PRESETS_VERSION,
                "presets": [
                    {"id": "ok", "name": "Good", "match": {"categories": ["Xbox"]}},
                    {"name": "missing-id"},
                    "not-an-object",
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(presets, "resource_path", lambda *a, **kw: good)
    out = presets.load_presets()
    assert len(out) == 1
    assert out[0].id == "ok"
