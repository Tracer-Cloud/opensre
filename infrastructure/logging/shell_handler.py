"""Root log handler for terminal surfaces that animate on the tty.

Two things go wrong when WARNING+ records reach a REPL's terminal through the
default path (Python's ``logging.lastResort`` → the ``sys.stderr`` captured at
import):

* Under ``prompt_toolkit.patch_stdout(raw=True)`` that stream bypasses the
  proxy that repairs bare ``\\n`` in raw mode, so the next line starts wherever
  the record ended.
* A record emitted from a worker thread while a Rich ``console.status``
  spinner animates races the spinner's ``\\r`` + erase-line frames on the same
  tty; a frame landing mid-line corrupts the terminal's cursor column, and
  everything rendered afterwards staircases across the screen.

:class:`ShellLogHandler` fixes both by printing through the surface's own Rich
console when one is provided: ``Console.print`` holds the console lock and a
running spinner repaints around it, so the record lands whole, above the
animation, at column 0. Without a console it writes to whatever ``sys.stderr``
is at emit time, which under ``patch_stdout`` is the proxy.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from rich.console import Console

ConsoleProvider = Callable[[], Console | None]


class ShellLogHandler(logging.Handler):
    """Emit records through the surface console, else the live ``sys.stderr``."""

    def __init__(self, console_provider: ConsoleProvider | None = None) -> None:
        super().__init__()
        self._console_provider = console_provider

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            console = self._console_provider() if self._console_provider else None
            if console is not None:
                # One terminal row per record. A record longer than the
                # console wraps in the terminal while a running Live spinner
                # still counts it as one row; its teardown cursor-up then
                # lands on the wrong row and everything after drifts.
                # markup=False / highlight=False: log text is not Rich markup.
                console.print(
                    message,
                    markup=False,
                    highlight=False,
                    overflow="ellipsis",
                    no_wrap=True,
                    crop=True,
                )
                return
            stream = sys.stderr
            stream.write(message + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


def install_shell_log_handler(
    console_provider: ConsoleProvider | None = None,
    *,
    level: int = logging.ERROR,
) -> None:
    """Install :class:`ShellLogHandler` on the root logger once.

    The floor is ERROR: the shell is a product surface, and a WARNING such as
    "[kubernetes] probe_access failed" duplicates what the surface already
    reports in its own words (the integrations table's ``failed`` row with a
    reason). Idempotent; a root logger that already has handlers (an embedding
    host, a test harness) is left alone.
    """
    root = logging.getLogger()
    if any(isinstance(handler, ShellLogHandler) for handler in root.handlers):
        return
    if root.handlers:
        return
    handler = ShellLogHandler(console_provider)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)


__all__ = ["ShellLogHandler", "install_shell_log_handler"]
