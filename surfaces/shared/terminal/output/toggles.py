from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

try:
    import select
    import termios
except ImportError:  # pragma: no cover - Windows fallback
    select = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]

_stdin_watcher_suppression_depth = 0
_stdin_watcher_lock = threading.Lock()
_tool_detail_toggle_callbacks: list[Callable[[], None]] = []
_TOOL_DETAIL_TOGGLE_BYTES = {b"\t"}  # Tab toggles tool-detail view during progress


@contextlib.contextmanager
def suppress_stdin_watchers() -> Iterator[None]:
    """Temporarily prevent raw stdin watcher threads from starting."""
    global _stdin_watcher_suppression_depth
    with _stdin_watcher_lock:
        _stdin_watcher_suppression_depth += 1
    try:
        yield
    finally:
        with _stdin_watcher_lock:
            _stdin_watcher_suppression_depth = max(0, _stdin_watcher_suppression_depth - 1)


def _stdin_watchers_suppressed() -> bool:
    with _stdin_watcher_lock:
        return _stdin_watcher_suppression_depth > 0


def register_tool_detail_toggle(callback: Callable[[], None]) -> Callable[[], None]:
    """Register a process-local Tab handler for the active progress view."""
    with _stdin_watcher_lock:
        _tool_detail_toggle_callbacks.append(callback)

    def _unregister() -> None:
        with _stdin_watcher_lock, contextlib.suppress(ValueError):
            _tool_detail_toggle_callbacks.remove(callback)

    return _unregister


def toggle_active_tool_details() -> bool:
    """Toggle the newest registered tool-detail view, if one exists."""
    with _stdin_watcher_lock:
        callback = _tool_detail_toggle_callbacks[-1] if _tool_detail_toggle_callbacks else None
    if callback is None:
        return False
    with contextlib.suppress(Exception):
        callback()
        return True
    return False


def _control_char(value: int, existing: Any) -> Any:
    if isinstance(existing, bytes):
        return bytes([value])
    if isinstance(existing, str):
        return chr(value)
    return value


def _disable_control_char(fd: int, existing: Any) -> Any:
    disabled = 0
    with contextlib.suppress(Exception):
        disabled = int(os.fpathconf(fd, "PC_VDISABLE"))
    if disabled < 0 or disabled > 255:
        disabled = 0
    return _control_char(disabled, existing)


class ToolDetailToggleWatcher:
    """Background stdin watcher for Tab without triggering terminal discard."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fd: int | None = None
        self._old_attrs: Any = None

    def start(self) -> None:
        if _stdin_watchers_suppressed() or select is None or termios is None:
            return
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return
        try:
            self._fd = sys.stdin.fileno()
            self._old_attrs = termios.tcgetattr(self._fd)
            new_attrs = termios.tcgetattr(self._fd)
            new_attrs[3] &= ~(termios.ICANON | termios.ECHO)
            if hasattr(termios, "IEXTEN"):
                new_attrs[3] &= ~termios.IEXTEN
            if hasattr(termios, "VMIN"):
                new_attrs[6][termios.VMIN] = 1
            if hasattr(termios, "VTIME"):
                new_attrs[6][termios.VTIME] = 0
            if hasattr(termios, "VDISCARD"):
                index = termios.VDISCARD
                new_attrs[6][index] = _disable_control_char(self._fd, new_attrs[6][index])
            termios.tcsetattr(self._fd, termios.TCSADRAIN, new_attrs)
        except Exception:
            self._fd = None
            self._old_attrs = None
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
        if self._fd is not None and self._old_attrs is not None and termios is not None:
            with contextlib.suppress(Exception):
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)
        from surfaces.shared.terminal.components.key_reader import restore_stdin_terminal

        restore_stdin_terminal()

    def _run(self) -> None:
        if self._fd is None or select is None:
            return
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([self._fd], [], [], 0.1)
            except Exception:
                return
            if not readable:
                continue
            try:
                data = os.read(self._fd, 1)
            except Exception:
                return
            if data in _TOOL_DETAIL_TOGGLE_BYTES:
                self._callback()
