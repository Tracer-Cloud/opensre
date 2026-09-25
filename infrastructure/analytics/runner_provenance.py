"""Read controller-provided execution evidence without exporting its credential."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from config.constants.analytics import (
    ANALYTICS_EXECUTION_CONTEXT_ENV,
    ANALYTICS_EXECUTION_CONTEXT_PATH,
    ANALYTICS_RUNNER_TOKEN_HEADER,
)


@dataclass(frozen=True)
class RunnerProvenance:
    """Reported launch context; only ingestion can verify runner ownership."""

    analytics_id: str
    execution_origin: str
    token: str = field(default="", repr=False)
    is_test: bool = False


def read_runner_provenance(
    environ: Mapping[str, str] | None = None,
    filesystem_root: Path | None = None,
) -> RunnerProvenance | None:
    """Read a bounded context file; missing or invalid evidence stays unknown."""
    values = os.environ if environ is None else environ
    root = Path("/") if filesystem_root is None else filesystem_root
    path = Path(
        values.get(ANALYTICS_EXECUTION_CONTEXT_ENV)
        or root / ANALYTICS_EXECUTION_CONTEXT_PATH.lstrip("/")
    )
    try:
        with path.open(encoding="utf-8") as stream:
            raw = stream.read(24_577)
        if len(raw) > 24_576:
            return None
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    identity, origin, token = (
        value.get(key) for key in ("analytics_id", "execution_origin", "token")
    )
    if not isinstance(identity, str) or not identity or len(identity) > 100:
        return None
    if origin not in ("github_actions", "scheduler", "agent"):
        return None
    if not isinstance(token, str) or len(token) > 16_384 or "\n" in token or "\r" in token:
        return None
    return RunnerProvenance(identity, origin, token, value.get("is_test") is True)


def execution_evidence(
    analytics_id: str, *, is_ci: bool, is_container: bool
) -> tuple[dict[str, str | bool], dict[str, str]]:
    """Return public evidence and private transport headers for this identity."""
    context = read_runner_provenance()
    if context is not None and context.analytics_id != analytics_id:
        context = None
    origin = context.execution_origin if context else "ci" if is_ci else "unknown"
    properties: dict[str, str | bool] = {
        "automation_status": "reported" if origin != "unknown" else "unknown",
        "execution_origin": origin,
    }
    headers = {}
    if context is not None and context.is_test:
        properties["is_test"] = True
    if context is not None and context.execution_origin == "github_actions":
        properties.update(
            is_ci=True,
            ci_detection_status="detected",
            execution_environment="ci_container" if is_container else "ci",
        )
        if context.token:
            headers[ANALYTICS_RUNNER_TOKEN_HEADER] = context.token
    return properties, headers
