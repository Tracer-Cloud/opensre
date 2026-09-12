"""Scoped paths for proactive-message persistence."""

from __future__ import annotations

from pathlib import Path

from config.constants.paths import session_home

_PROACTIVE_MESSAGES_DIR = "proactive_messages"
_DECISIONS_FILENAME = "decisions.jsonl"
_CURSORS_FILENAME = "cursors.jsonl"


def proactive_messages_dir() -> Path:
    """Return this actor's proactive-message storage directory."""
    return session_home() / _PROACTIVE_MESSAGES_DIR


def decision_ledger_path() -> Path:
    """Return this actor's append-only decision ledger path."""
    return proactive_messages_dir() / _DECISIONS_FILENAME


def judgement_cursor_path() -> Path:
    """Return this actor's append-only judgement cursor path."""
    return proactive_messages_dir() / _CURSORS_FILENAME


__all__ = ["decision_ledger_path", "judgement_cursor_path", "proactive_messages_dir"]
