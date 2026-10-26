"""Record output printed before the full-screen app starts."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TextIO, cast

from surfaces.interactive_shell.ui.transcript_view.store import TranscriptStore


class _TranscriptTee:
    """Write through to the terminal and copy the text into the transcript."""

    def __init__(self, target: TextIO, store: TranscriptStore) -> None:
        self._target = target
        self._store = store

    def write(self, data: str) -> int:
        written = self._target.write(data)
        self._store.append_text(data, on_normal_screen=True)
        return written

    def flush(self) -> None:
        self._target.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


@contextmanager
def record_startup_output(store: TranscriptStore) -> Iterator[None]:
    """Copy everything printed to stdout in this block into ``store``."""
    original = sys.stdout
    sys.stdout = cast(TextIO, _TranscriptTee(original, store))
    try:
        yield
    finally:
        sys.stdout = original


__all__ = ["record_startup_output"]
