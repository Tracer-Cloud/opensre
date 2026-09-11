"""Local delivery inbox for interactive-shell loop runs."""

from __future__ import annotations

import json
import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from config.constants import OPENSRE_HOME_DIR
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
)
from infrastructure.scheduling.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)

_INBOX_FILENAME = "scheduler_loop_messages.jsonl"


@dataclass(frozen=True, slots=True)
class LocalLoopMessage:
    """One locally persisted loop delivery."""

    message_id: str
    task_id: str
    loop_id: str
    name: str
    created_at: str
    message: str
    prompt: str


def _default_inbox_path() -> Path:
    return OPENSRE_HOME_DIR / _INBOX_FILENAME


def _lock_path(inbox_path: Path) -> Path:
    return inbox_path.with_suffix(".lock")


def record_loop_message(
    task: ScheduledTask,
    message: str,
    *,
    inbox_path: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Append ``message`` to the local interactive-shell loop inbox."""
    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    created_at = timestamp.astimezone(UTC).isoformat()
    message_id = f"local:{timestamp.astimezone(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
    path = inbox_path or _default_inbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "message_id": message_id,
        "task_id": task.id,
        "loop_id": task.params.get(LOOP_GROUP_ID_PARAM, task.id),
        "name": task.name,
        "created_at": created_at,
        "message": message,
        "prompt": task.params.get(LOOP_PROMPT_PARAM, ""),
    }

    lock = FileLock(_lock_path(path))
    with lock, path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")
    return message_id


def get_loop_messages(
    *,
    limit: int = 20,
    inbox_path: Path | None = None,
) -> list[LocalLoopMessage]:
    """Return local loop messages, newest first."""
    path = inbox_path or _default_inbox_path()
    if limit <= 0 or not path.exists():
        return []

    messages: list[LocalLoopMessage] = []
    for raw_line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if not raw_line.strip():
            continue
        try:
            entry: dict[str, Any] = json.loads(raw_line)
            messages.append(
                LocalLoopMessage(
                    message_id=str(entry.get("message_id") or ""),
                    task_id=str(entry.get("task_id") or ""),
                    loop_id=str(entry.get("loop_id") or ""),
                    name=str(entry.get("name") or ""),
                    created_at=str(entry.get("created_at") or ""),
                    message=str(entry.get("message") or ""),
                    prompt=str(entry.get("prompt") or ""),
                )
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.debug("Skipping invalid local loop message: %s", exc)
            continue
        if len(messages) >= limit:
            break
    return messages


def find_loop_reports(
    message_keys: Collection[tuple[str, str]], *, inbox_path: Path | None = None
) -> dict[tuple[str, str], str]:
    """Find legacy reports by exact task and delivery IDs, without an inbox lookback limit."""
    path = inbox_path or _default_inbox_path()
    remaining = set(message_keys)
    if not remaining or not path.exists():
        return {}
    reports: dict[tuple[str, str], str] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue
            key = (entry.get("task_id"), entry.get("message_id"))
            if not all(isinstance(value, str) for value in key):
                continue
            if key in remaining and isinstance(entry.get("message"), str):
                reports[key] = entry["message"]
                remaining.remove(key)
                if not remaining:
                    break
    return reports


__all__ = [
    "LocalLoopMessage",
    "get_loop_messages",
    "find_loop_reports",
    "record_loop_message",
]
