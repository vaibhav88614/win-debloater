"""Services control - protections and validation."""
from __future__ import annotations

from app.core import services


def test_protected_set_includes_known_critical_services():
    for name in ("samss", "winmgmt", "rpcss", "trustedinstaller", "windefend"):
        assert name in services.PROTECTED_SERVICES


def test_safe_tweakable_catalog_has_friendly_names():
    assert "diagtrack" in services.SAFE_TWEAKABLE
    assert services.SAFE_TWEAKABLE["diagtrack"]


def test_set_start_type_rejects_protected_service():
    res = services.set_start_type("WinMgmt", "Disabled")
    assert not res.ok
    assert "protected" in res.error.lower()


def test_set_start_type_rejects_invalid_value():
    res = services.set_start_type("PrintNotify", "Bogus")
    assert not res.ok
    assert "invalid" in res.error.lower()


def test_stop_service_rejects_protected_service():
    res = services.stop_service("Winmgmt")
    assert not res.ok
    assert "protected" in res.error.lower()


def test_service_info_safe_property():
    safe = services.ServiceInfo(
        name="DiagTrack", display_name="DiagTrack",
        is_safe_tweak=True, is_protected=False,
    )
    assert safe.safe is True

    protected_safe = services.ServiceInfo(
        name="DiagTrack", display_name="DiagTrack",
        is_safe_tweak=True, is_protected=True,
    )
    assert protected_safe.safe is False

    unknown = services.ServiceInfo(
        name="SomeRandomService", display_name="SomeRandomService",
        is_safe_tweak=False, is_protected=False,
    )
    assert unknown.safe is False
