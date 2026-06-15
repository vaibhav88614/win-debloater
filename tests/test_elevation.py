"""Argument-quoting helpers in the elevation module."""

from __future__ import annotations

from app.core import elevation


def test_subprocess_args_quotes_spaces_and_quotes():
    out = elevation.subprocess_args(["plain", "with space", 'has"quote', ""])
    assert out == 'plain "with space" "has\\"quote" ""'


def test_subprocess_args_empty_list():
    assert elevation.subprocess_args([]) == ""


def test_is_admin_returns_bool():
    # Test environment is usually not elevated, but we only care about the type.
    assert isinstance(elevation.is_admin(), bool)
