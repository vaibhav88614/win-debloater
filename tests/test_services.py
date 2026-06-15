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


def test_set_start_type_rejects_empty_name():
    res = services.set_start_type("", "Manual")
    assert not res.ok
    assert "required" in res.error.lower()


def test_stop_service_rejects_empty_name():
    res = services.stop_service("   ")
    assert not res.ok
    assert "required" in res.error.lower()


def test_start_service_rejects_empty_name():
    res = services.start_service("")
    assert not res.ok
    assert "required" in res.error.lower()


def test_set_start_type_quotes_apostrophes(monkeypatch):
    """Names with apostrophes must not break the PS command."""
    captured = {}

    def fake_run(script, *, timeout=60):
        captured["script"] = script
        from app.core import powershell as ps

        return ps.PSResult(ok=True, returncode=0)

    from app.core import powershell as ps

    monkeypatch.setattr(ps, "run", fake_run)

    res = services.set_start_type("Bing's Service", "Manual")
    assert res.ok
    # Apostrophe doubled and value wrapped in single quotes.
    assert "'Bing''s Service'" in captured["script"]
    assert "Bing's Service" not in captured["script"].replace("''", "")


def test_stop_service_quotes_apostrophes(monkeypatch):
    captured = {}
    from app.core import powershell as ps

    monkeypatch.setattr(
        ps,
        "run",
        lambda script, *, timeout=60: (
            captured.update(script=script),
            ps.PSResult(ok=True, returncode=0),
        )[1],
    )
    services.stop_service("It's Fine")
    assert "'It''s Fine'" in captured["script"]


def test_service_info_safe_property():
    safe = services.ServiceInfo(
        name="DiagTrack",
        display_name="DiagTrack",
        is_safe_tweak=True,
        is_protected=False,
    )
    assert safe.safe is True

    protected_safe = services.ServiceInfo(
        name="DiagTrack",
        display_name="DiagTrack",
        is_safe_tweak=True,
        is_protected=True,
    )
    assert protected_safe.safe is False

    unknown = services.ServiceInfo(
        name="SomeRandomService",
        display_name="SomeRandomService",
        is_safe_tweak=False,
        is_protected=False,
    )
    assert unknown.safe is False


# -------------------------- list_services parser ---------------------------

from types import SimpleNamespace


def _ps_result(items):
    """Build a PSResult-like object whose .items mimics powershell.PSResult.items."""
    return SimpleNamespace(ok=True, items=items)


def test_list_services_parses_typical_rows(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        services.ps,
        "run_json",
        lambda *a, **kw: _ps_result(
            [
                {
                    "Name": "Spooler",
                    "DisplayName": "Print Spooler",
                    "State": "Running",
                    "StartMode": "Auto",
                    "Description": "Loads files to memory.",
                    "CanStop": True,
                },
                {
                    "Name": "wuauserv",
                    "DisplayName": "Windows Update",
                    "State": "Running",
                    "StartMode": "Auto",
                    "Description": "Update service.",
                    "CanStop": True,
                },
                {
                    "Name": "DiagTrack",
                    "DisplayName": "Connected User Experiences",
                    "State": "Running",
                    "StartMode": "Auto",
                    "Description": "Telemetry.",
                    "CanStop": True,
                },
            ]
        ),
    )
    out = services.list_services()
    names = {s.name for s in out}
    assert {"Spooler", "wuauserv", "DiagTrack"} <= names

    wu = next(s for s in out if s.name == "wuauserv")
    assert wu.is_protected is True  # in PROTECTED_SERVICES catalog

    diagtrack = next(s for s in out if s.name == "DiagTrack")
    assert diagtrack.is_safe_tweak is True
    assert diagtrack.is_protected is False


def test_list_services_skips_empty_name(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        services.ps,
        "run_json",
        lambda *a, **kw: _ps_result(
            [
                {
                    "Name": "",
                    "DisplayName": "Nothing",
                    "State": "Stopped",
                    "StartMode": "Manual",
                    "Description": "",
                    "CanStop": False,
                },
                {
                    "Name": "Spooler",
                    "DisplayName": "Print Spooler",
                    "State": "Running",
                    "StartMode": "Auto",
                    "Description": "",
                    "CanStop": True,
                },
            ]
        ),
    )
    out = services.list_services()
    assert [s.name for s in out] == ["Spooler"]


def test_list_services_returns_empty_when_ps_failed(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        services.ps, "run_json", lambda *a, **kw: SimpleNamespace(ok=False, items=[])
    )
    assert services.list_services() == []


def test_list_services_returns_empty_on_non_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    out = services.list_services()
    assert out == []
