"""Rich console adapter for REPL dispatch streaming and cancellation."""

from __future__ import annotations

import sys
import threading
from typing import IO, Any, Protocol

from rich.console import Console, RenderableType, ThemeContext
from rich.file_proxy import FileProxy
from rich.status import Status
from rich.style import StyleType
from rich.text import Text
from rich.theme import Theme


class _PromptSpinner(Protocol):
    bytes_in: int
    streaming: bool

    def stop(self) -> None:
        """Stop the prompt spinner."""


_ROW_SAFE_KWARGS = frozenset({"width"})


def _renders_as_rows(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    """One renderable printed with default options: safe to route through the buffered path."""
    return len(args) == 1 and set(kwargs) <= _ROW_SAFE_KWARGS


def _as_renderable(args: tuple[Any, ...]) -> Any:
    renderable = args[0]
    if isinstance(renderable, str):
        return Text.from_markup(renderable)
    return renderable


class StreamingConsole(Console):
    """Console adapter for streaming progress + cancellation checks."""

    #: Set before ``Console.__init__`` runs, which reaches the ``file`` property.
    _output: Console | None = None
    #: Backing store for ``record`` while ``Console.__init__`` assigns it.
    _record_flag: bool = False

    def __init__(
        self,
        spinner: _PromptSpinner,
        cancel_event: threading.Event,
        *,
        output: Console | None = None,
        **kwargs: Any,
    ) -> None:
        """Stream one turn, writing through ``output`` when a caller supplies it.

        ``capture()`` and ``record`` buffer inside the ``Console`` instance
        rather than at its file, so writing to a copy of the file leaves a
        caller's capture empty. Rendering through the console object itself is
        what makes those see the turn.

        When ``output`` is set, REPL TTY column prep is skipped — the caller's
        stream is not the interactive prompt TTY.
        """
        super().__init__(**kwargs)
        self._spinner = spinner
        self._cancel_event = cancel_event
        self._output = output

    def update_streaming_progress(self, bytes_received: int) -> None:
        self._spinner.bytes_in = bytes_received

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Print with the TTY column reset and CRLF row endings when not streaming.

        Tool output (a chart, a report) is rendered to a buffer and written in
        one call with ``\\r\\n`` rows: under the pinned prompt's raw stdout
        patching, row-by-row ``\\n`` output makes the status bar repaint one
        line lower per row, stacking stale copies of itself down the screen.
        """
        if self._output is not None:
            self._output.print(*args, **kwargs)
            return
        if not self._spinner.streaming and not isinstance(sys.stdout, FileProxy):
            from surfaces.shared.terminal.components.choice_menu import (
                ensure_tty_column_zero,
                prepare_repl_output_line,
            )
            from surfaces.shared.terminal.components.rendering import (
                _repl_output_already_prepared,
                _repl_table_width,
                print_repl_renderable,
            )

            if not args and not kwargs:
                ensure_tty_column_zero()
            elif not _repl_output_already_prepared():
                prepare_repl_output_line()
            if sys.stdout.isatty() and "width" not in kwargs:
                kwargs["width"] = _repl_table_width(self)
            if _renders_as_rows(args, kwargs) and self.file is sys.stdout and sys.stdout.isatty():
                print_repl_renderable(self, _as_renderable(args))
                return
        super().print(*args, **kwargs)

    def use_theme(self, theme: Theme, *, inherit: bool = True) -> ThemeContext:
        """Push ``theme`` onto the console this turn renders through."""
        if self._output is not None:
            return self._output.use_theme(theme, inherit=inherit)
        return super().use_theme(theme, inherit=inherit)

    def status(
        self,
        status: RenderableType,
        *,
        spinner: str = "dots",
        spinner_style: StyleType = "status.spinner",
        speed: float = 1.0,
        refresh_per_second: float = 12.5,
    ) -> Status:
        """Render the spinner on the console this turn renders through.

        Defaults are copied from ``rich.console.Console.status`` — if Rich
        changes them, this pins the old values silently.
        """
        kwargs: dict[str, Any] = {
            "spinner": spinner,
            "spinner_style": spinner_style,
            "speed": speed,
            "refresh_per_second": refresh_per_second,
        }
        if self._output is not None:
            return self._output.status(status, **kwargs)
        return super().status(status, **kwargs)

    @property
    def record(self) -> bool:
        """Recording state of the console this turn renders through.

        ``print`` delegates to ``_output``, so segments only ever reach that
        console's record buffer. Toggling ``record`` here must therefore toggle
        it on the delegate, or ``capture_console_segment`` records nothing.
        """
        if self._output is not None:
            return self._output.record
        return self._record_flag

    @record.setter
    def record(self, value: bool) -> None:
        if self._output is not None:
            self._output.record = value
            return
        self._record_flag = value

    def export_text(self, *, clear: bool = True, styles: bool = False) -> str:
        """Export recorded text from the console this turn renders through."""
        if self._output is not None:
            return self._output.export_text(clear=clear, styles=styles)
        return super().export_text(clear=clear, styles=styles)

    @property
    def file(self) -> IO[str]:
        """Track the target console's current file rather than a snapshot."""
        if self._output is not None:
            return self._output.file
        return super().file

    @file.setter
    def file(self, new_file: IO[str]) -> None:
        if self._output is not None:
            self._output.file = new_file
            return
        self._file = new_file


__all__ = ["StreamingConsole"]
