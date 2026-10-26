"""Full-screen transcript: a width-independent store and the view that draws it."""

from surfaces.interactive_shell.ui.transcript_view.control import TranscriptControl
from surfaces.interactive_shell.ui.transcript_view.store import (
    TranscriptEntry,
    TranscriptStore,
    render_for_scrollback,
    render_text,
)
from surfaces.interactive_shell.ui.transcript_view.tee import record_startup_output

__all__ = [
    "TranscriptControl",
    "TranscriptEntry",
    "TranscriptStore",
    "record_startup_output",
    "render_for_scrollback",
    "render_text",
]
