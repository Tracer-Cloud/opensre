"""Spinner glyphs that every hosting terminal draws in one cell."""

from __future__ import annotations

import os

from config.constants.terminal_host import APPLE_TERMINAL_PROGRAM, TERM_PROGRAM_ENV

BRAILLE_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
# Apple Terminal draws braille through a fallback font whose glyphs bleed into
# the next cell, leaving fragments behind when the row is repainted.
DOT_SPINNER_FRAMES = ("·", "•", "●", "•")


def spinner_frames(term_program: str | None = None) -> tuple[str, ...]:
    """Frames for the live spinner, chosen for the terminal drawing them."""
    program = term_program if term_program is not None else os.environ.get(TERM_PROGRAM_ENV, "")
    if program == APPLE_TERMINAL_PROGRAM:
        return DOT_SPINNER_FRAMES
    return BRAILLE_SPINNER_FRAMES


__all__ = ["BRAILLE_SPINNER_FRAMES", "DOT_SPINNER_FRAMES", "spinner_frames"]
