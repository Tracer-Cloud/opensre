"""Tests for ReplSubprocessPresenter Rich markup escaping."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import (
    ReplSubprocessPresenter,
    _escape_markup_message,
    _highlight_command,
)
from surfaces.interactive_shell.session import Session


def _styled_spans(command: str) -> set[str]:
    text = _highlight_command(command)
    return {text.plain[span.start : span.end].strip() for span in text.spans}


def _presenter() -> tuple[ReplSubprocessPresenter, StringIO]:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, width=80, color_system=None)
    session = Session()
    return ReplSubprocessPresenter(session, console), buffer


def test_long_command_output_is_stashed_for_ctrl_o() -> None:
    presenter, buffer = _presenter()
    body = "\n".join(f"line {i}" for i in range(30))
    presenter.print_command_output(body)
    assert presenter.session.terminal.collapsed_tool_output == body
    assert "Ctrl+O to view" in buffer.getvalue()


def test_short_command_output_is_not_stashed() -> None:
    presenter, _buffer = _presenter()
    presenter.print_command_output("ok")
    assert presenter.session.terminal.collapsed_tool_output is None


def test_plain_command_output_is_recessed_but_keeps_command_colours() -> None:
    # Raw stdout reads as supporting detail beneath the bright reply: plain output
    # is recessed to SECONDARY, while colour the command emitted itself survives.
    import io
    import re

    from infrastructure.terminal import theme as ui_theme

    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        width=80,
        no_color=False,
    )
    presenter = ReplSubprocessPresenter(Session(), console)

    presenter.print_command_output("plain line of output")
    presenter.print_command_output("\x1b[32mgreen success\x1b[0m")

    raw = buffer.getvalue()
    # Compare against SECONDARY rendered through an identical console rather than
    # hard-coding the truecolor SGR. Rich caches the rendered ANSI on the Style
    # object, and ``Style.parse`` is process-wide lru_cached, so whichever test
    # renders this colour first fixes its escape sequence for the whole run --
    # an 8-colour console elsewhere in the shard would otherwise make this assert
    # a stale ``\x1b[37m``. Both sides here share that cache, so the check holds
    # at any colour depth.
    reference = io.StringIO()
    Console(
        file=reference,
        force_terminal=True,
        color_system="truecolor",
        width=80,
        no_color=False,
    ).print("x", style=str(ui_theme.SECONDARY))
    secondary_sgr = reference.getvalue().split("x")[0]
    assert secondary_sgr and secondary_sgr in raw  # plain output recessed to SECONDARY
    assert re.search(r"\x1b\[[0-9;]*32m", raw)  # command's own green survives the base


def test_escape_markup_message_preserves_intentional_tags() -> None:
    escaped = _escape_markup_message("[error]failed[/]")
    buffer = StringIO()
    Console(file=buffer, force_terminal=True, width=80, color_system=None).print(escaped)
    output = buffer.getvalue()
    assert "failed" in output


def test_escape_markup_message_escapes_plain_markup() -> None:
    escaped = _escape_markup_message("Grafana [prod] rate[5m]")
    buffer = StringIO()
    Console(file=buffer, force_terminal=True, width=80, color_system=None).print(escaped)
    output = buffer.getvalue()
    assert "[prod]" in output
    assert "rate[5m]" in output


def test_escape_markup_message_escapes_dynamic_text_inside_tags() -> None:
    escaped = _escape_markup_message("[bold]task [critical][/bold]")
    assert "task" in escaped
    assert "[critical]" in escaped


def test_print_error_escapes_dynamic_markup() -> None:
    presenter, buffer = _presenter()
    presenter.print_error("failed: [bold]bad[/]")
    output = buffer.getvalue()
    assert "[bold]bad[/]" in output
    assert "bad" in output


def test_print_escapes_untrusted_suffix_via_print_error_pattern() -> None:
    presenter, buffer = _presenter()
    presenter.print_error("command failed to start: [bold]bad[/]")
    output = buffer.getvalue()
    assert "[bold]bad[/]" in output


def test_print_preserves_task_id_markup_with_escaped_brackets() -> None:
    presenter, buffer = _presenter()
    task_id = "task-[critical]"
    presenter.print(
        f"[dim]synthetic test started — task[/] [bold]{task_id}[/bold]. "
        "[highlight]/tasks[/] [dim]to monitor.[/]"
    )
    output = buffer.getvalue()
    assert task_id in output
    assert "/tasks" in output


def test_highlight_command_leaves_quoted_operators_plain() -> None:
    styled = _styled_spans('echo "a && b"')
    assert "echo" in styled
    assert "&&" not in styled
    assert "b" not in styled


def test_highlight_command_leaves_quoted_flags_plain() -> None:
    styled = _styled_spans('echo "use --force"')
    assert "echo" in styled
    assert "--force" not in styled


def test_highlight_command_leaves_quoted_pipes_plain() -> None:
    styled = _styled_spans('grep -E "(foo|bar)" file')
    assert "grep" in styled
    assert "-E" in styled
    assert "|" not in styled
    assert "bar" not in styled


def test_highlight_command_still_colours_unquoted_syntax_after_quotes() -> None:
    command = 'echo "a && b" && echo done'
    quoted_and = command.index("&&")
    unquoted_and = command.index("&&", quoted_and + 2)
    text = _highlight_command(command)
    operator_starts = {
        span.start for span in text.spans if command[span.start : span.end].strip() == "&&"
    }
    assert unquoted_and in operator_starts
    assert quoted_and not in operator_starts
    styled = {command[span.start : span.end].strip() for span in text.spans}
    assert "echo" in styled
    assert "done" not in styled


def test_highlight_command_leaves_single_quoted_syntax_plain() -> None:
    styled = _styled_spans("echo 'a && b --force'")
    assert "echo" in styled
    assert "&&" not in styled
    assert "--force" not in styled


def test_highlight_command_ignores_escaped_quotes_inside_double_quotes() -> None:
    styled = _styled_spans(r'echo "say \"--force\""')
    assert "echo" in styled
    assert "--force" not in styled
