"""Shell log handler: records go through the surface console, atomically with spinners."""

from __future__ import annotations

import io
import logging
import sys
import threading

import pytest
from rich.console import Console

from infrastructure.logging.shell_handler import ShellLogHandler, install_shell_log_handler


def _isolated_logger(name: str, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.propagate = False
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    return logger


def test_record_prints_through_the_provided_console() -> None:
    # Arrange — a console the surface renders on, and a handler bound to it
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    handler = ShellLogHandler(lambda: console)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _isolated_logger("test.shell_handler.console", handler)

    # Act
    logger.warning("[kubernetes] probe_access failed")

    # Assert — landed on the console, not on sys.stderr
    assert buf.getvalue() == "[kubernetes] probe_access failed\n"


def test_record_from_a_worker_thread_lands_whole_while_a_spinner_is_live() -> None:
    """The race behind the staircase: a probe thread logs while status() animates."""
    # Arrange
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    handler = ShellLogHandler(lambda: console)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _isolated_logger("test.shell_handler.spinner", handler)
    message = "Retrying (Retry(total=2)) after connection broken: Connection refused"

    # Act — log from a worker thread while the spinner holds the console
    with console.status("Verifying integrations…", spinner="dots"):
        worker = threading.Thread(target=lambda: logger.warning(message))
        worker.start()
        worker.join(5.0)

    # Assert — the message appears once, on its own line, uninterrupted
    out = buf.getvalue()
    assert out.count(message) == 1
    assert message + "\n" in out


def test_a_record_wider_than_the_console_takes_exactly_one_row() -> None:
    """A wrapped record desyncs a live spinner's row accounting; crop instead.

    Rich's Live counts each printed record as one row and rewinds the cursor by
    that count on teardown. A record that wraps in the terminal breaks the count
    and every table row rendered afterwards starts further from column zero.
    """
    # Arrange — a 60-column console and a 300-char urllib3-style retry warning
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=60)
    handler = ShellLogHandler(lambda: console)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _isolated_logger("test.shell_handler.onerow", handler)

    # Act
    logger.warning("Retrying (Retry(total=2)) after connection broken by " + "x" * 250)

    # Assert — one terminal row, cropped with an ellipsis, never wrapped
    out = buf.getvalue()
    assert out.count("\n") == 1
    first_row = out.split("\n")[0]
    assert len(first_row) == 60
    assert first_row.endswith("…")


def test_falls_back_to_the_live_stderr_without_a_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stream swapped in after construction (patch_stdout) still receives it."""
    # Arrange
    original = io.StringIO()
    later = io.StringIO()
    monkeypatch.setattr(sys, "stderr", original)
    handler = ShellLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _isolated_logger("test.shell_handler.stderr", handler)
    monkeypatch.setattr(sys, "stderr", later)

    # Act
    logger.warning("late")

    # Assert
    assert later.getvalue() == "late\n"
    assert original.getvalue() == ""


def test_install_is_idempotent_on_an_unconfigured_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange — a truly empty root (pytest's own capture handlers hidden)
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])

    # Act
    install_shell_log_handler()
    install_shell_log_handler()

    # Assert — one handler, ERROR floor: WARNINGs duplicate what the surface
    # already reports (a failed probe is a table row, not a log line)
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], ShellLogHandler)
    assert root.handlers[0].level == logging.ERROR


def test_install_leaves_a_host_configured_root_logger_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange — an embedding host already configured logging
    root = logging.getLogger()
    host_handler = logging.NullHandler()
    monkeypatch.setattr(root, "handlers", [host_handler])

    # Act
    install_shell_log_handler()

    # Assert
    assert root.handlers == [host_handler]
