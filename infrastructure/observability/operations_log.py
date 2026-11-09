"""Best-effort local JSONL log for operational lifecycle events."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from config.constants import (
    DEFAULT_OPENSRE_OPERATIONS_LOG_MAX_BYTES,
    OPENSRE_HOME_DIR,
    OPENSRE_OPERATIONS_LOG_DISABLED_ENV,
    OPENSRE_OPERATIONS_LOG_FILENAME,
    OPENSRE_OPERATIONS_LOG_MAX_BYTES_ENV,
    OPENSRE_OPERATIONS_LOG_PATH_ENV,
)
from infrastructure.observability.trace.redaction import redact_sensitive

logger = logging.getLogger(__name__)

_FALSE_VALUES = frozenset({"", "0", "false", "off", "no"})
_TEXT_MAX_CHARS = 500
_DICT_MAX_ITEMS = 80
_LIST_MAX_ITEMS = 80
_SCHEMA_VERSION = 1

_lock = threading.Lock()


def operations_log_path() -> Path:
    """Return the local operations log path."""
    override = os.getenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return OPENSRE_HOME_DIR / OPENSRE_OPERATIONS_LOG_FILENAME


def record_operation(
    event: str,
    data: Mapping[str, Any] | None = None,
    *,
    path: Path | None = None,
) -> None:
    """Append one local operation event.

    This logger is a diagnostic aid, not a source of product truth. It never
    raises on filesystem or serialization failures.
    """
    log_path = path or operations_log_path()
    if not _enabled(log_path, explicit_path=path is not None):
        return

    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "schema_version": _SCHEMA_VERSION,
        "event": event,
        "pid": os.getpid(),
        "context": _bound_context(),
        "data": _safe_json_value(dict(data or {})),
    }
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    try:
        with _lock:
            _rotate_if_needed(log_path, len(line))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        logger.debug("failed to write operations log to %s", log_path, exc_info=True)


def read_operations(
    *,
    limit: int = 100,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read the most recent operation entries."""
    if limit <= 0:
        return []
    log_path = path or operations_log_path()
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    entries: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        with contextlib.suppress(json.JSONDecodeError):
            value = json.loads(line)
            if isinstance(value, dict):
                entries.append(value)
    return entries


def _enabled(log_path: Path, *, explicit_path: bool) -> bool:
    disabled = os.getenv(OPENSRE_OPERATIONS_LOG_DISABLED_ENV, "").strip().lower()
    if disabled not in _FALSE_VALUES:
        return False
    # Keep unit tests off the developer's real home directory. Tests that need
    # this log set OPENSRE_OPERATIONS_LOG_PATH explicitly to a temp file.
    if (
        os.getenv("PYTEST_CURRENT_TEST")
        and not explicit_path
        and not os.getenv(OPENSRE_OPERATIONS_LOG_PATH_ENV)
    ):
        return False
    if (
        os.getenv("CI", "").strip().lower() not in _FALSE_VALUES
        and not explicit_path
        and not os.getenv(OPENSRE_OPERATIONS_LOG_PATH_ENV)
    ):
        return False
    return bool(log_path.name)


def _bound_context() -> dict[str, str]:
    context: dict[str, str] = {}
    with contextlib.suppress(Exception):
        from infrastructure.observability.trace.spans import current_trace_session_id

        session_id = current_trace_session_id()
        if session_id:
            context["session_id"] = session_id
    with contextlib.suppress(Exception):
        from infrastructure.analytics.repl_context import (
            get_cli_session_id,
            get_cli_turn_kind,
            get_prompt_turn_id,
        )

        cli_session_id = get_cli_session_id()
        cli_turn_kind = get_cli_turn_kind()
        prompt_turn_id = get_prompt_turn_id()
        if cli_session_id and "session_id" not in context:
            context["session_id"] = cli_session_id
        if cli_turn_kind:
            context["turn_kind"] = cli_turn_kind
        if prompt_turn_id:
            context["prompt_turn_id"] = prompt_turn_id
    return context


def _rotate_if_needed(path: Path, next_line_bytes: int) -> None:
    max_bytes = _max_bytes()
    with contextlib.suppress(OSError):
        if path.exists() and path.stat().st_size + next_line_bytes > max_bytes:
            backup = path.with_name(path.name + ".1")
            with contextlib.suppress(OSError):
                if backup.exists():
                    backup.unlink()
            path.rename(backup)


def _max_bytes() -> int:
    raw = os.getenv(OPENSRE_OPERATIONS_LOG_MAX_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_OPENSRE_OPERATIONS_LOG_MAX_BYTES
    with contextlib.suppress(ValueError):
        value = int(raw)
        if value > 0:
            return value
    return DEFAULT_OPENSRE_OPERATIONS_LOG_MAX_BYTES


def _safe_json_value(value: Any) -> Any:
    redacted = redact_sensitive(value)
    return _jsonable(redacted)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _compact_text(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        items = list(value.items())[:_DICT_MAX_ITEMS]
        rendered = {str(key): _jsonable(item) for key, item in items}
        if len(value) > _DICT_MAX_ITEMS:
            rendered["_truncated_items"] = len(value) - _DICT_MAX_ITEMS
        return rendered
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)[:_LIST_MAX_ITEMS]
        rendered_list = [_jsonable(item) for item in items]
        if len(value) > _LIST_MAX_ITEMS:
            rendered_list.append({"_truncated_items": len(value) - _LIST_MAX_ITEMS})
        return rendered_list
    return _compact_text(str(value))


def _compact_text(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) <= _TEXT_MAX_CHARS:
        return compact
    return compact[: _TEXT_MAX_CHARS - 15].rstrip() + "... [truncated]"


__all__ = ["operations_log_path", "read_operations", "record_operation"]
