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


def derive_name(lines: list[str]) -> str:
    """Derive a human-readable session name from the first substantive turn.

    Prefers turn_detail.prompt (full text) over the turn stub. Falls back
    to the empty string if no usable turn exists.
    """
    # Prefer v2 message entries.
    for line in lines[1:]:
        with contextlib.suppress(json.JSONDecodeError):
            rec = json.loads(line)
            if rec.get("type") == "message" and rec.get("role") == "user":
                metadata = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
                kind = metadata.get("kind", "chat")
                if kind in CHAT_KINDS | {"alert"}:
                    text = (rec.get("content") or "").strip().replace("\n", " ")
                    if text:
                        return text[:_NAME_MAX_CHARS] + ("…" if len(text) > _NAME_MAX_CHARS else "")
    # Prefer first turn_detail (has full prompt, no truncation)
    for line in lines[1:]:
        with contextlib.suppress(json.JSONDecodeError):
            rec = json.loads(line)
            if rec.get("type") == "turn_detail" and rec.get("kind") in CHAT_KINDS | {"alert"}:
                text = (rec.get("prompt") or "").strip().replace("\n", " ")
                if text:
                    return text[:_NAME_MAX_CHARS] + ("…" if len(text) > _NAME_MAX_CHARS else "")
    # Fall back to turn stub text (covers cli_agent/follow_up/alert kinds)
    for line in lines[1:]:
        with contextlib.suppress(json.JSONDecodeError):
            rec = json.loads(line)
            is_v1_turn = rec.get("type") == "turn"
            is_v2_stub = (
                rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub"
            )
            if (is_v1_turn or is_v2_stub) and rec.get("kind") in CHAT_KINDS | {
                "alert",
                "incoming_alert",
            }:
                text = (rec.get("text") or "").strip().replace("\n", " ")
                if text:
                    return text[:_NAME_MAX_CHARS] + ("…" if len(text) > _NAME_MAX_CHARS else "")
    return ""
