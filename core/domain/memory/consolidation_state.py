"""What consolidation last did for a memory directory (``.consolidation.json``).

Holds when it last ran — the cooldown — and a fingerprint of the input its
last summary was written from, so an unchanged store is not summarized again.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.domain.memory.files import (
    CONSOLIDATION_STATE_FILENAME,
    read_json_object,
    write_json_atomically,
)

_LAST_RUN_KEY = "last_run_at"
_SUMMARY_INPUT_KEY = "summary_input"


@dataclass(frozen=True)
class ConsolidationState:
    """Last consolidation start (UTC) and the fingerprint of the last summarized input."""

    last_run_at: datetime | None = None
    summary_input: str = ""


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
    )


def write_consolidation_state_unlocked(directory: Path, state: ConsolidationState) -> None:
    """Persist ``state``; the caller holds the memory lock. May raise ``OSError``."""
    payload: dict[str, object] = {}
    if state.last_run_at is not None:
        payload[_LAST_RUN_KEY] = state.last_run_at.isoformat(timespec="seconds")
    if state.summary_input:
        payload[_SUMMARY_INPUT_KEY] = state.summary_input
    write_json_atomically(_state_path(directory), payload)


def clear_consolidation_state_unlocked(directory: Path) -> None:
    """Forget the last run so the next session start consolidates again."""
    with contextlib.suppress(FileNotFoundError):
        _state_path(directory).unlink()


__all__ = [
    "ConsolidationState",
    "clear_consolidation_state_unlocked",
    "read_consolidation_state",
    "write_consolidation_state_unlocked",
]
