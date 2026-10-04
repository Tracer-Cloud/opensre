"""What consolidation last did for a memory directory (``.consolidation.json``).

Holds when it last ran — the cooldown — a fingerprint of the input its last
summary was written from, so an unchanged store is not summarized again, and
when a memory was last forgotten, so nothing recorded before that is
summarized again.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from core.domain.memory.files import (
    CONSOLIDATION_STATE_FILENAME,
    read_json_object,
    write_json_atomically,
)

_LAST_RUN_KEY = "last_run_at"
_SUMMARY_INPUT_KEY = "summary_input"
_FORGOTTEN_KEY = "forgotten_at"


@dataclass(frozen=True)
class ConsolidationState:
    """Last consolidation start, last summarized input, and last forget (UTC)."""

    last_run_at: datetime | None = None
    summary_input: str = ""
    forgotten_at: datetime | None = None


def _state_path(directory: Path) -> Path:
    return directory / CONSOLIDATION_STATE_FILENAME


def _parse_stamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def read_consolidation_state(directory: Path) -> ConsolidationState:
    """The stored state; empty when consolidation never ran or the file is unreadable."""
    raw = read_json_object(_state_path(directory))
    fingerprint = raw.get(_SUMMARY_INPUT_KEY)
    return ConsolidationState(
        last_run_at=_parse_stamp(raw.get(_LAST_RUN_KEY)),
        summary_input=fingerprint if isinstance(fingerprint, str) else "",
        forgotten_at=_parse_stamp(raw.get(_FORGOTTEN_KEY)),
    )


def write_consolidation_state_unlocked(directory: Path, state: ConsolidationState) -> None:
    """Persist ``state``; the caller holds the memory lock. May raise ``OSError``."""
    payload: dict[str, object] = {}
    if state.last_run_at is not None:
        payload[_LAST_RUN_KEY] = state.last_run_at.isoformat(timespec="seconds")
    if state.summary_input:
        payload[_SUMMARY_INPUT_KEY] = state.summary_input
    if state.forgotten_at is not None:
        # Full precision: session summaries and session starts are compared with it.
        payload[_FORGOTTEN_KEY] = state.forgotten_at.isoformat()
    write_json_atomically(_state_path(directory), payload)


def record_forget_unlocked(directory: Path, *, now: datetime | None = None) -> None:
    """Note that a memory was forgotten; the caller holds the memory lock.

    Clears the cooldown and the summarized-input fingerprint so the next
    consolidation writes a fresh summary, and records the moment so session
    summaries recorded up to it are never summarized again. May raise ``OSError``.
    """
    state = read_consolidation_state(directory)
    write_consolidation_state_unlocked(
        directory,
        replace(
            state,
            last_run_at=None,
            summary_input="",
            forgotten_at=now or datetime.now(UTC),
        ),
    )


__all__ = [
    "ConsolidationState",
    "read_consolidation_state",
    "record_forget_unlocked",
    "write_consolidation_state_unlocked",
]
