"""Filesystem helpers shared by the JSONL session storage and repository.

One JSONL file per session lives under ``~/.opensre/sessions/``. Both the
per-session storage writer and the cross-session repository resolve paths and
derive display names through these helpers so the on-disk layout has a single
owner.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from core.agent_harness.session.persistence.contracts import CHAT_KINDS

_NAME_MAX_CHARS = 50


def sessions_dir() -> Path:
    from config.constants.paths import get_sessions_dir

    return get_sessions_dir()


def session_path(session_id: str) -> Path:
    return sessions_dir() / f"{session_id}.jsonl"


def _display_name(text: str, slash_commands: set[str]) -> str:
    text = text.strip().replace("\n", " ")
    if not text or text in slash_commands:
        return ""
    return text[:_NAME_MAX_CHARS] + ("…" if len(text) > _NAME_MAX_CHARS else "")


def derive_name(lines: list[str]) -> str:
    """Derive a human-readable session name from the first substantive turn.

    Prefers turn_detail.prompt (full text) over the turn stub. Falls back
    to the empty string if no usable turn exists. Explicit slash invocations
    mirrored into chat messages are not session topics.
    """
    records = []
    for line in lines[1:]:
        with contextlib.suppress(json.JSONDecodeError):
            records.append(json.loads(line))
    slash_commands = {
        (rec.get("text") or "").strip().replace("\n", " ")
        for rec in records
        if rec.get("kind") == "slash"
        and (
            rec.get("type") == "turn"
            or (rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub")
        )
    }
    # A picker may record resolved arguments while its agent turn keeps the bare command.
    slash_commands |= {text.split(maxsplit=1)[0] for text in slash_commands if text}
    # Prefer v2 message entries.
    for rec in records:
        if rec.get("type") == "message" and rec.get("role") == "user":
            metadata = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
            kind = metadata.get("kind", "chat")
            if kind in CHAT_KINDS | {"alert"}:
                text = _display_name(rec.get("content") or "", slash_commands)
                if text:
                    return text
    # Prefer first turn_detail (has full prompt, no truncation)
    for rec in records:
        if rec.get("type") == "turn_detail" and rec.get("kind") in CHAT_KINDS | {"alert"}:
            text = _display_name(rec.get("prompt") or "", slash_commands)
            if text:
                return text
    # Fall back to turn stub text (covers cli_agent/follow_up/alert kinds)
    for rec in records:
        is_v1_turn = rec.get("type") == "turn"
        is_v2_stub = rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub"
        if (is_v1_turn or is_v2_stub) and rec.get("kind") in CHAT_KINDS | {
            "alert",
            "incoming_alert",
        }:
            text = _display_name(rec.get("text") or "", slash_commands)
            if text:
                return text
    return ""
