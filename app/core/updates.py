"""Lightweight GitHub release update check using only the standard library.

QtNetwork is intentionally excluded from the packaged build, so this uses
``urllib`` (run from a worker thread by the UI). Every failure is swallowed and
reported as ``None`` so an update check can never crash or block the app.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from app import __version__
from app.core.applog import get_logger

# GitHub "owner/repo" queried for the latest release.
DEFAULT_REPO = "vaibhav88614/win-debloater"

_API = "https://api.github.com/repos/{repo}/releases/latest"
_NUM = re.compile(r"\d+")


def parse_version(value: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple of ints.

    Tolerates a leading ``v`` and non-numeric suffixes (e.g. ``1.2.0-rc1``).
    """
    value = (value or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in value.split("."):
        m = _NUM.match(chunk)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly newer version than ``current``."""
    return parse_version(latest) > parse_version(current)


def check_for_update(
    repo: str = DEFAULT_REPO, *, current: str = __version__, timeout: int = 5
) -> dict | None:
    """Query the latest GitHub release.

    Returns ``{"latest": str, "url": str, "newer": bool}`` or ``None`` on any
    network/parse error (so callers can fail silently).
    """
    log = get_logger()
    url = _API.format(repo=repo)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "win-debloater-update-check",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        log.info("Update check failed: %s", exc)
        return None

    tag = data.get("tag_name") or data.get("name") or ""
    if not tag:
        return None
    return {
        "latest": tag.lstrip("vV"),
        "url": data.get("html_url") or "",
        "newer": is_newer(tag, current),
    }
