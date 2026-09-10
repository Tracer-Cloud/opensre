"""Read completed interaction boundaries from append-only session JSONL."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from core.agent_harness.session.persistence.paths import session_path
from infrastructure.proactive_messages.models import ProactiveInteraction, ProactiveTrigger

_MAX_INTERACTION_MESSAGE_CHARS = 24_000


def latest_session_record_id(session_id: str) -> str | None:
    """Return the last valid entry id in a session file, excluding its header."""
    records = _read_session_records(session_path(session_id))
    for record in reversed(records):
        record_id = str(record.get("id") or "").strip()
        if record_id:
            return record_id
    return None


def latest_completed_interaction_boundary(
    session_id: str,
) -> tuple[str | None, str] | None:
    """Return record ids bounding the newest persisted user/assistant exchange."""
    records = _read_session_records(session_path(session_id))
    if not records:
        return None
    assistant_index = next(
        (
            index
            for index in range(len(records) - 1, -1, -1)
            if records[index].get("type") == "message" and records[index].get("role") == "assistant"
        ),
        None,
    )
    if assistant_index is None:
        return None
    user_index = next(
        (
            index
            for index in range(assistant_index - 1, -1, -1)
            if records[index].get("type") == "message" and records[index].get("role") == "user"
        ),
        None,
    )
    if user_index is None:
        return None
    start_record_id = None
    if user_index > 0:
        start_record_id = str(records[user_index - 1].get("id") or "").strip() or None
    end_record_id = str(records[-1].get("id") or "").strip()
    if not end_record_id:
        return None
    return start_record_id, end_record_id


def load_trigger_interaction(trigger: ProactiveTrigger) -> ProactiveInteraction | None:
    """Load the persisted messages inside ``trigger``'s exact record boundary."""
    records = _read_session_records(session_path(trigger.session_id))
    if not records:
        return None
    start_index = _record_index(records, trigger.start_record_id)
    if trigger.start_record_id is not None and start_index is None:
        return None
    end_index = _record_index(records, trigger.end_record_id)
    if end_index is None:
        return None
    first = 0 if start_index is None else start_index + 1
    if first > end_index:
        return None

    bounded: list[tuple[str, str, str]] = []
    for record in records[first : end_index + 1]:
        if record.get("type") != "message":
            continue
        role = str(record.get("role") or "")
        content = str(record.get("content") or "").strip()
        record_id = str(record.get("id") or "")
        if role in {"user", "assistant"} and content and record_id:
            bounded.append((role, content[:_MAX_INTERACTION_MESSAGE_CHARS], record_id))
    first_user_index = next(
        (index for index, (role, _content, _record_id) in enumerate(bounded) if role == "user"),
        None,
    )
    if first_user_index is None:
        return None
    messages_after_user = bounded[first_user_index:]
    assistant_rows = [row for row in messages_after_user if row[0] == "assistant"]
    if not assistant_rows:
        return None
    interaction_id = assistant_rows[-1][2]
    return ProactiveInteraction(
        interaction_id=interaction_id,
        end_record_id=trigger.end_record_id,
        messages=tuple((role, content) for role, content, _record_id in messages_after_user),
        user_message=messages_after_user[0][1],
        agent_outcome=assistant_rows[-1][1],
    )


def _record_index(records: list[dict[str, Any]], record_id: str | None) -> int | None:
    if record_id is None:
        return None
    return next(
        (index for index, record in enumerate(records) if str(record.get("id") or "") == record_id),
        None,
    )


def _read_session_records(path: Path) -> list[dict[str, Any]]:
    with contextlib.suppress(OSError):
        lines = path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[1:]:
            with contextlib.suppress(json.JSONDecodeError):
                record = json.loads(line)
                if isinstance(record, dict) and record.get("id"):
                    records.append(record)
        return records
    return []


__all__ = [
    "latest_completed_interaction_boundary",
    "latest_session_record_id",
    "load_trigger_interaction",
]
