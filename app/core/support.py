"""Bundle logs, diagnostics, and recent history into a single zip for support."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.core import diagnostics
from app.core.applog import log_file_path

MAX_HISTORY = 100


def create_support_bundle(
    dest_zip: str | Path,
    actions,
    *,
    diagnostics_text: str | None = None,
    max_history: int = MAX_HISTORY,
) -> Path:
    """Write a support zip containing logs, diagnostics, and recent history.

    ``actions`` is a list of :class:`~app.core.actionlog.Action` (newest first).
    Returns the path written.
    """
    dest = Path(dest_zip)
    log_path = log_file_path()

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        # Rotating log + its backups (app.log, app.log.1 ... app.log.5).
        candidates = [log_path] + [Path(f"{log_path}.{i}") for i in range(1, 6)]
        for p in candidates:
            try:
                if p.exists():
                    zf.write(p, arcname=f"logs/{p.name}")
            except OSError:
                pass

        zf.writestr("diagnostics.txt", diagnostics_text or diagnostics.as_text())

        history = [a.to_dict() for a in list(actions)[:max_history]]
        zf.writestr("history.json", json.dumps(history, indent=2, ensure_ascii=False))

    return dest
