"""Command output is framed under its ``$ command`` header, tracebacks collapsed."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from surfaces.shared.terminal.tables.tables import print_command_output


def _out(text: str, *, width: int) -> str:
    console = Console(file=StringIO(), force_terminal=False, width=width)
    print_command_output(console, text)
    return console.file.getvalue()


def test_short_result_is_indented_under_the_header_with_a_marker() -> None:
    out = _out("done", width=80)
    assert "↳ done" in out  # parent → child hierarchy


def test_multiline_result_that_fits_indents_with_one_marker() -> None:
    out = _out("first\nsecond", width=80)
    assert "↳ first" in out
    assert "second" in out
    assert out.count("↳") == 1  # marker on the first line only


def test_wide_line_is_still_framed_with_the_gutter() -> None:
    # A wide line no longer flushes the block; it is framed and wraps within it.
    out = _out("x" * 40, width=20)
    assert "↳" in out


def test_a_wide_line_does_not_strip_the_gutter_from_narrow_siblings() -> None:
    # Regression: one wide line used to flush the whole block — narrow rows too —
    # to the left margin. Each block is framed regardless of a lone wide line.
    out = _out("short\n" + "x" * 40, width=20)
    assert "↳ short" in out


def test_a_python_traceback_is_collapsed_to_the_exception_and_last_frame() -> None:
    trace = (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 10, in main\n'
        "    run()\n"
        '  File "app.py", line 4, in run\n'
        "    raise ValueError('boom')\n"
        "ValueError: boom"
    )
    out = _out(trace, width=100)

    assert "↳ ValueError: boom" in out  # the exception is the headline
    assert "at app.py:4 in run" in out  # innermost frame — where it raised
    assert "1 more frame hidden" in out  # the outer frame is folded
    assert "run()" not in out  # intermediate source lines are gone


def test_long_output_collapses_to_a_peek_with_an_expand_marker() -> None:
    """Droid-style: a short head plus ``Ctrl+O to view``; full text is stashed."""
    out = _out("\n".join(f"line {i}" for i in range(30)), width=100)
    assert "↳ line 0" in out
    assert "line 3" in out
    assert "line 29" not in out
    assert "Ctrl+O to view" in out


def test_non_traceback_output_is_left_intact() -> None:
    out = _out("Traceback: a log line that merely mentions the word", width=100)
    assert "a log line that merely mentions the word" in out  # not folded
