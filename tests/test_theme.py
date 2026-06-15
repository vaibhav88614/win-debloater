"""Tests for the theme stylesheet builder."""

from __future__ import annotations

from app.ui import theme


def test_themes_listed():
    assert theme.THEMES == ("dark", "light")
    assert theme.DEFAULT_THEME in theme.THEMES


def test_normalize_falls_back_to_default():
    assert theme.normalize("dark") == "dark"
    assert theme.normalize("light") == "light"
    assert theme.normalize("LIGHT") == "light"
    assert theme.normalize("nonsense") == theme.DEFAULT_THEME
    assert theme.normalize("") == theme.DEFAULT_THEME
    assert theme.normalize(None) == theme.DEFAULT_THEME


def test_build_qss_has_no_unsubstituted_tokens():
    for name in theme.THEMES:
        qss = theme.build_qss(name)
        assert "$" not in qss, f"unsubstituted token in {name} theme"
        assert "QTableView" in qss  # new model/view tables are styled
        assert "QMenu" in qss


def test_dark_and_light_differ():
    assert theme.build_qss("dark") != theme.build_qss("light")


def test_dark_preserves_original_accent():
    # The accent colour from the original style.qss must be retained.
    assert "#3a6df0" in theme.build_qss("dark")
