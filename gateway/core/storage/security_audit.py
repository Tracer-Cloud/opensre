"""Append-only record of security-relevant gateway actions.

One JSON object per line under ``~/.opensre/gateway/security-audit.jsonl``,
covering approval create/resolve/expire, so the record outlives the process
that wrote it.

Scope is narrower than a typical audit log. The path is host-global, not
per-org. The lock serializes writers in one process only: concurrent
gateway workers append to the same file without coordination, so
cross-process order is whatever the filesystem gives. This is not a
shared store for a multi-worker or multi-host deployment. Writes are
best-effort: a failed append is logged and swallowed so it never fails
the caller's action. ``detail`` is caller-supplied and unvalidated;
keeping bodies and secrets out of it is a contract with callers, not
enforced here.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.constants.paths import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_AUDIT_REL = Path("gateway") / "security-audit.jsonl"
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def security_audit_path() -> Path:
    """Return the append-only audit file path (host home, not org silo)."""
    return OPENSRE_HOME_DIR / _AUDIT_REL


def _serialize_record(record: dict[str, Any]) -> str | None:
    try:
        return json.dumps(record, separators=(",", ":"), sort_keys=True, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("security audit serialize failed: %s", type(exc).__name__)
        return None


def _harden_permissions(path: Path) -> None:
    """Tighten modes where the filesystem allows it; a refusal is not fatal."""
    with contextlib.suppress(OSError):
        path.parent.chmod(_DIR_MODE)
    with contextlib.suppress(OSError):
        os.chmod(path, _FILE_MODE)


def audit_security_action(
    *,
    action: str,
    platform: str | None = None,
    actor_id: str | None = None,
    chat_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one structured audit record and mirror it to the process log.

    ``detail`` must not contain plaintext message bodies or secret values.
    Prefer ids, enums, and short hashes. Append failures are logged and ignored.
    """
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "platform": platform or "",
        "actor_id": actor_id or "",
        "chat_id": chat_id or "",
        "resource_type": resource_type or "",
        "resource_id": resource_id or "",
        "outcome": outcome or "",
    }
    if detail:
        record["detail"] = detail

    line = _serialize_record(record)
    if line is None:
        return

    path = security_audit_path()
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            _harden_permissions(path)
    except OSError as exc:
        logger.warning("security audit write failed: %s", type(exc).__name__)

    logger.info(
        "[security-audit] action=%s platform=%s actor=%s resource=%s/%s outcome=%s",
        action,
        record["platform"] or "N/A",
        record["actor_id"] or "N/A",
        record["resource_type"] or "N/A",
        record["resource_id"] or "N/A",
        record["outcome"] or "N/A",
    )


__all__ = ["audit_security_action", "security_audit_path"]
