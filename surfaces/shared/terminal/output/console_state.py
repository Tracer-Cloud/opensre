from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from rich.console import Console

_live_console: Console | None = None
_active_display: Any | None = None
_completed_footer_snapshot: tuple[str, float, str, str] | None = None
_tracker_toggle_stop_fn: Callable[[], None] | None = None
_turn_spinner: Any | None = None
_repl_state: Any | None = None


def set_tracker_toggle_stop_fn(fn: Callable[[], None] | None) -> None:
    """Register callback used to stop tracker-owned keyboard watchers."""
    global _tracker_toggle_stop_fn
    _tracker_toggle_stop_fn = fn


def set_turn_spinner(spinner: Any | None) -> None:
    """Register the active turn's prompt spinner so displays can animate it.

    Lets rendering helpers drive the spinner with phase labels (``set_phase``)
    and stop it (``stop``) while the turn runs.
    """
    global _turn_spinner
    _turn_spinner = spinner


def get_turn_spinner() -> Any | None:
    return _turn_spinner


def set_repl_state(state: Any | None) -> None:
    """Register the live ``ReplState`` so a tool update can pin the gateway plan."""
    global _repl_state
    _repl_state = state


def get_repl_state() -> Any | None:
    return _repl_state


def _capture_footer_snapshot(display: Any) -> None:
    """Record the phase footer fields visible when a display stops."""
    global _completed_footer_snapshot
    if display is None:
        return
    t0 = getattr(display, "_t0", None)
    if t0 is None:
        return
    _completed_footer_snapshot = (
        getattr(display, "_current_phase", ""),
        time.monotonic() - t0,
        getattr(display, "_model", ""),
        getattr(display, "_mode", "local"),
    )


def consume_footer_snapshot() -> tuple[str, float, str, str] | None:
    global _completed_footer_snapshot
    snapshot, _completed_footer_snapshot = _completed_footer_snapshot, None
    return snapshot


def _get_console() -> Console:
    """Return the active Live console when running, else a fresh one."""
    return _live_console or Console(highlight=False)


def set_live_console(console: Console | None) -> None:
    global _live_console
    _live_console = console


def unregister_live_console(expected: Console | None) -> None:
    global _live_console
    if expected is not None and _live_console is expected:
        _live_console = None


def set_active_display(display: Any | None) -> None:
    global _active_display
    _active_display = display


def clear_active_display(expected: Any) -> None:
    global _active_display
    if _active_display is expected:
        _active_display = None


def stop_display() -> None:
    """Stop any running live display before printing final report output."""
    if _active_display is not None:
        _active_display.stop()

    if _tracker_toggle_stop_fn is not None:
        _tracker_toggle_stop_fn()
