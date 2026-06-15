"""Curated selection presets for the Bloatware tab.

A preset is a named bundle that picks installed AppX packages by category and/or
explicit catalog id. Presets are defined declaratively in
``app/core/data/presets.json`` so they can be edited without code changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.core.applog import get_logger
from app.core.paths import resource_path

PRESETS_VERSION = 1


@dataclass
class Preset:
    id: str
    name: str
    description: str = ""
    categories: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    safe_only: bool = False


def load_presets() -> list[Preset]:
    """Load and validate the bundled presets file."""
    log = get_logger()
    path = resource_path("app", "core", "data", "presets.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to load presets: %s", exc)
        return []

    if data.get("version") != PRESETS_VERSION:
        log.warning(
            "Presets file version mismatch: expected %s, got %r",
            PRESETS_VERSION,
            data.get("version"),
        )

    out: list[Preset] = []
    for idx, raw in enumerate(data.get("presets", []) or []):
        if not isinstance(raw, dict):
            log.warning("Preset #%d is not an object; skipped.", idx)
            continue
        pid = raw.get("id")
        name = raw.get("name")
        if not pid or not name:
            log.warning("Preset #%d missing id/name; skipped: %r", idx, raw)
            continue
        match = raw.get("match", {}) or {}
        out.append(
            Preset(
                id=str(pid),
                name=str(name),
                description=str(raw.get("description") or ""),
                categories=[str(c).lower() for c in match.get("categories", []) or []],
                ids=[str(i).lower() for i in match.get("ids", []) or []],
                safe_only=bool(raw.get("safe_only", False)),
            )
        )
    return out


def apply_preset(preset: Preset, packages) -> list:
    """Return the subset of ``packages`` matched by ``preset``.

    Matching is OR-logic: an installed package is included if its catalog
    category matches OR its name matches an explicit id (case-insensitive).
    Non-removable packages are kept — the UI still gates them on Advanced mode.
    """
    cats = set(preset.categories)
    explicit = set(preset.ids)
    matched = []
    for pkg in packages:
        if preset.safe_only and not getattr(pkg, "safe", True):
            continue
        if cats and (pkg.category or "").lower() in cats:
            matched.append(pkg)
            continue
        if explicit and pkg.name.lower() in explicit:
            matched.append(pkg)
            continue
    return matched
