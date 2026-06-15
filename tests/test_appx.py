"""Catalog loading and pattern matching for AppX packages."""
from __future__ import annotations

from app.core import appx


def test_load_catalog_returns_packages():
    catalog = appx.load_catalog()
    assert isinstance(catalog, list)
    assert len(catalog) > 10
    # Every entry has an id and a name.
    for entry in catalog:
        assert "id" in entry and entry["id"]
        assert "name" in entry


def test_match_catalog_exact_id():
    catalog = appx.load_catalog()
    entry = appx._match_catalog("Microsoft.BingNews", catalog)
    assert entry is not None
    assert entry["id"] == "Microsoft.BingNews"


def test_match_catalog_wildcard_pattern():
    catalog = appx.load_catalog()
    # "*.CandyCrush" exists in the catalog and should match a real-world name.
    entry = appx._match_catalog("king.com.CandyCrush", catalog)
    assert entry is not None
    # king.com.* should match this too; either rule wins, but a match must exist.


def test_match_catalog_unknown_returns_none():
    catalog = appx.load_catalog()
    assert appx._match_catalog("NotAnAppEverInstalled.Xyz", catalog) is None


def test_appx_package_display_name_prefers_friendly():
    pkg = appx.AppxPackage(name="Microsoft.BingNews", full_name="...", friendly_name="News")
    assert pkg.display_name == "News"
    pkg2 = appx.AppxPackage(name="Microsoft.BingNews", full_name="...")
    assert pkg2.display_name == "Microsoft.BingNews"
