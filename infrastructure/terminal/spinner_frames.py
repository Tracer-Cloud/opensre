"""Spinner glyphs for the live "Thinking…" row."""

from __future__ import annotations

BRAILLE_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def spinner_frames() -> tuple[str, ...]:
    """Frames for the live spinner: braille in every terminal."""
    return BRAILLE_SPINNER_FRAMES


__all__ = ["BRAILLE_SPINNER_FRAMES", "spinner_frames"]
