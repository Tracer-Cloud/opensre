"""Tests for Rich console capture used by slash analytics."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.telemetry.console_capture import capture_console_segment


def test_capture_console_segment_clears_recording_buffer_between_uses() -> None:
    console = Console(file=io.StringIO(), record=False, width=120)

    with capture_console_segment(console) as get_first:
        console.print("first")
    assert get_first() == "first"

    with capture_console_segment(console) as get_second:
        console.print("second")
    assert get_second() == "second"
    assert console.record is False


def test_capture_console_segment_preserves_prior_recording_when_already_enabled() -> None:
    console = Console(file=io.StringIO(), record=True, width=120)
    console.print("before")

    with capture_console_segment(console) as get_segment:
        console.print("during")
    assert get_segment() == "during"
    assert "before" in console.export_text(clear=False)
