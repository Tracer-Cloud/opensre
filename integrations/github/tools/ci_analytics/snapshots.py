"""On-disk snapshots of CI reliability reports, shared by the loop tick and the tool.

A snapshot is the report's raw figures plus when they were computed. The
loop writes one per tick; the tool writes one per live analysis and reuses a
fresh one for the same repository and window instead of reading GitHub
again, which matters for benchmark repositories whose 30-day history takes
minutes to read.
"""

from __future__ import annotations

import dataclasses
import json
import types
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from config.constants.paths import OPENSRE_HOME_DIR
from integrations.github.tools.ci_analytics.models import CiAnalyticsReport

SNAPSHOT_DIRNAME = "ci_reliability_reports"
SNAPSHOT_MAX_AGE_HOURS = 24


def snapshot_root(root: Path | None = None) -> Path:
    return root or OPENSRE_HOME_DIR / SNAPSHOT_DIRNAME


def _folder(root: Path, owner: str, repo: str) -> Path:
    """One directory per repository, nested so ``foo/bar-baz`` and ``foo-bar/baz`` never meet."""
    return root / owner / repo


def write_snapshot(
    root: Path, owner: str, repo: str, now: datetime, payload: dict[str, Any]
) -> Path:
    """Write ``payload`` under ``<root>/<owner>/<repo>/<timestamp>.json`` and return the path.

    The owner and repository are stored in the file too, so a read can check
    that a snapshot belongs to the repository it is answering for.
    """
    target = _folder(root, owner, repo) / f"{now:%Y-%m-%dT%H%M%SZ}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    stamped = {**payload, "owner": owner, "repo": repo}
    target.write_text(json.dumps(stamped, indent=2, sort_keys=True, default=str), encoding="utf-8")
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
    folder = _folder(root, owner, repo)
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
        if payload.get("owner") != owner or payload.get("repo") != repo:
            continue
        if int(payload.get("window_days", -1)) != window_days:
            continue
        if (now - generated).total_seconds() > max_age_hours * 3600:
            return None
        payload["snapshot_path"] = str(path)
        return payload
    return None


def report_to_dict(report: CiAnalyticsReport) -> dict[str, Any]:
    """The report as JSON-ready data: datetimes as ISO strings, enums as values."""
    return cast(dict[str, Any], _to_json_value(dataclasses.asdict(report)))


def report_from_dict(data: Mapping[str, Any]) -> CiAnalyticsReport:
    """Rebuild a report saved by :func:`report_to_dict`."""
    built = _from_json_value(CiAnalyticsReport, dict(data))
    return cast(CiAnalyticsReport, built)


def _to_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _from_json_value(annotation: Any, value: Any) -> Any:
    """Rebuild ``value`` as ``annotation``: dataclasses, datetimes, enums, tuples, optionals."""
    if value is None:
        return None
    origin = get_origin(annotation)
    if origin in (types.UnionType, Union):
        inner = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _from_json_value(inner[0], value) if len(inner) == 1 else value
    if origin is tuple:
        item_type = get_args(annotation)[0]
        return tuple(_from_json_value(item_type, item) for item in value)
    if annotation is datetime:
        return datetime.fromisoformat(str(value))
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if dataclasses.is_dataclass(annotation) and isinstance(value, Mapping):
        hints = get_type_hints(annotation)
        kwargs = {
            field.name: _from_json_value(hints[field.name], value.get(field.name))
            for field in dataclasses.fields(annotation)
            if field.name in value
        }
        return cast(Any, annotation)(**kwargs)
    return value


__all__ = [
    "SNAPSHOT_DIRNAME",
    "SNAPSHOT_MAX_AGE_HOURS",
    "read_fresh_snapshot",
    "report_from_dict",
    "report_to_dict",
    "snapshot_root",
    "write_snapshot",
]
