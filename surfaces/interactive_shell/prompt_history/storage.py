"""Persistent command history for the interactive shell prompt."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit.history import History, InMemoryHistory

from surfaces.interactive_shell.prompt_history.policy import (
    HistoryPolicy,
    RedactingFileHistory,
    RefreshingFileHistory,
    history_file_lock,
)

_HISTORY_FILENAME = "interactive_history"


def prompt_history_path() -> Path:
    from config.constants import OPENSRE_HOME_DIR

    return OPENSRE_HOME_DIR / _HISTORY_FILENAME


def load_prompt_history(policy: HistoryPolicy | None = None) -> History:
    """Use persistent prompt history when possible, with an in-memory fallback.

    When ``policy.enabled`` is False, persistence is skipped entirely and
    an in-memory ring is returned. When ``policy.redact`` is False, raw
    ``FileHistory`` is used so power users can opt out of redaction.
    """
    settings = policy or _load_policy()

    if not settings.enabled:
        return InMemoryHistory()

    try:
        path = prompt_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if settings.redact:
            return RedactingFileHistory(str(path), max_entries=settings.max_entries)
        return RefreshingFileHistory(str(path))
    except OSError:
        return InMemoryHistory()


def load_command_history_entries() -> list[str]:
    """Return persisted prompt entries in chronological order."""
    try:
        path = prompt_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        history = RefreshingFileHistory(str(path))
        raw = list(reversed(list(history.load_history_strings())))
        return [line.rstrip("\r\n") for line in raw]
    except OSError:
        return []


def clear_persisted_history() -> bool:
    """Truncate the on-disk history file. Returns True if the file is gone or empty."""
    path = prompt_history_path()
    try:
        with history_file_lock(path):
            if path.exists():
                path.write_text("", encoding="utf-8")
        return True
    except OSError:
        return False


def _load_policy() -> HistoryPolicy:
    from config.repl_config import read_history_settings

    return HistoryPolicy.load(read_history_settings())


__all__ = [
    "clear_persisted_history",
    "load_command_history_entries",
    "load_prompt_history",
    "prompt_history_path",
]
