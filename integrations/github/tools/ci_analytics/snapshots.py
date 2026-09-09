"""On-disk snapshots of CI reliability reports, shared by the loop tick and the tool.

A snapshot is the report's raw figures plus when they were computed. The
loop writes one per tick; the tool writes one per live analysis and reuses a
fresh one for the same repository and window instead of reading GitHub
again, which matters for benchmark repositories whose 30-day history takes
minutes to read.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.constants.paths import OPENSRE_HOME_DIR

SNAPSHOT_DIRNAME = "ci_reliability_reports"
SNAPSHOT_MAX_AGE_HOURS = 24


def snapshot_root(root: Path | None = None) -> Path:
    return root or OPENSRE_HOME_DIR / SNAPSHOT_DIRNAME


def write_snapshot(
    root: Path, owner: str, repo: str, now: datetime, payload: dict[str, Any]
) -> Path:
    """Write ``payload`` under ``<root>/<owner>-<repo>/<timestamp>.json`` and return the path."""
    target = root / f"{owner}-{repo}" / f"{now:%Y-%m-%dT%H%M%SZ}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return target


def read_fresh_snapshot(
    root: Path,
    owner: str,
    repo: str,
    *,
    window_days: int,
    now: datetime,
    max_age_hours: int = SNAPSHOT_MAX_AGE_HOURS,
) -> dict[str, Any] | None:
    """The newest snapshot for ``owner/repo`` with the same window, if young enough."""
    folder = root / f"{owner}-{repo}"
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json"), reverse=True):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            generated = datetime.fromisoformat(str(loaded["generated_at"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(loaded, dict):
            continue
        payload: dict[str, Any] = dict(loaded)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=UTC)
        if int(payload.get("window_days", -1)) != window_days:
            continue
        if (now - generated).total_seconds() > max_age_hours * 3600:
            return None
        payload["snapshot_path"] = str(path)
        return payload
    return None


__all__ = [
    "SNAPSHOT_DIRNAME",
    "SNAPSHOT_MAX_AGE_HOURS",
    "read_fresh_snapshot",
    "snapshot_root",
    "write_snapshot",
]
