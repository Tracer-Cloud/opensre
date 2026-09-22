"""Stable, literal filtering for searchable choice panels."""

from __future__ import annotations

from collections.abc import Sequence

from infrastructure.safety.terminal_output import strip_terminal_controls


def searchable_text(labels: Sequence[str], notes: Sequence[str] | None = None) -> list[str]:
    """Normalize visible labels and metadata once without interpreting patterns."""
    return [
        strip_terminal_controls(f"{label} {notes[index] if notes else ''}").casefold()
        for index, label in enumerate(labels)
    ]


def matching_indices(texts: Sequence[str], query: str) -> list[int]:
    """Return original indexes matching every whitespace-separated query word."""
    words = query.casefold().split()
    return [index for index, text in enumerate(texts) if all(word in text for word in words)]
