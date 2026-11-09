"""Missing-condition `/goal set` opens the styled input form through real dispatch."""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.command_inputs import needs_command_input
from config.constants import OPENSRE_INTERACTIVE_ENV
from surfaces.interactive_shell.command_registry import dispatch_slash, input_collection
from surfaces.interactive_shell.command_registry import help as help_cmd
from surfaces.interactive_shell.command_registry.session_cmds import goal as goal_cmd
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.goal_input import build_goal_set_form, parse_max_turns


@pytest.fixture(autouse=True)
def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)


def run_keys(app: Any, keys: str) -> Any:
    """Drive the actual prompt-toolkit bindings with a deterministic input pipe."""
    with create_pipe_input() as pipe:
        app.input = pipe
        app.output = DummyOutput()
        return app.run(pre_run=lambda: pipe.send_text(keys))


def dispatch(command: str, *, tty: bool = True) -> tuple[str, Session]:
    session = Session()
    session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
        command, session
    )
    output = io.StringIO()
    dispatch_slash(command, session, Console(file=output), is_tty=tty)
    return output.getvalue(), session


def test_parse_max_turns_matches_direct_command_semantics() -> None:
    assert parse_max_turns("") == 0
    assert parse_max_turns("4") == 4
    assert parse_max_turns("0") == 1
    assert parse_max_turns("-2") == 1
    with pytest.raises(ValueError):
        parse_max_turns("many")
    with pytest.raises(ValueError):
        parse_max_turns("", allow_blank=False)
    with pytest.raises(ValueError):
        parse_max_turns("   ", allow_blank=False)


@pytest.mark.parametrize("via_help", [False, True])
def test_goal_set_form_attaches_with_unbounded_default(
    monkeypatch: pytest.MonkeyPatch, via_help: bool
) -> None:
    def collect(app: Any) -> Any:
        return run_keys(app, "All auth tests pass\r\x13")

    monkeypatch.setattr(goal_cmd, "run_command_input", collect)
    command = "/goal set"
    if via_help:
        monkeypatch.setattr(help_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(help_cmd, "browse_help_commands", lambda _sections: "/goal")
        monkeypatch.setattr(help_cmd, "repl_choose_subcommand", lambda **_kw: "set")
        command = "/help"
    with create_app_session(output=DummyOutput()):
        output, session = dispatch(command)
    assert session.session_goal is not None
    assert session.session_goal.condition == "All auth tests pass"
    assert session.session_goal.max_outer_turns == 0
    assert session.terminal.pending_prompt_default == "All auth tests pass"
    assert session.terminal.pending_prompt_autosubmit is True
    assert "◎ /goal active" in output


def test_goal_set_form_preserves_max_turns_and_reports_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def collect(app: Any) -> Any:
        # Invalid supplied value errors on submit; fix it without retyping the condition.
        return run_keys(app, "Ship the fix\r\x13\x01\x0b5\r\x13")

    monkeypatch.setattr(goal_cmd, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        _output, session = dispatch("/goal set --max-turns many")
    assert session.session_goal is not None
    assert session.session_goal.condition == "Ship the fix"
    assert session.session_goal.max_outer_turns == 5


def test_goal_set_form_rejects_blank_condition() -> None:
    with create_app_session(output=DummyOutput()):
        values = run_keys(build_goal_set_form({}), "\rValid condition\r\x13")
    assert values is not None
    assert values.condition == "Valid condition"
    assert values.max_turns == 0


@pytest.mark.parametrize("columns,rows", [(80, 30), (40, 12)])
def test_goal_set_form_guides_the_initial_condition_without_an_error(
    columns: int, rows: int
) -> None:

    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    captured: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_goal_set_form({})
        app.input = pipe

        def capture_initial_form(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or captured:
                return
            captured.extend(
                "".join(screen.data_buffer[y][x].char for x in range(columns))
                for y in range(screen.height)
            )
            pipe.send_text("\x03")

        app.after_render += capture_initial_form
        result = app.run()

    rendered = "\n".join(captured)
    assert result is None
    assert "Condition · required" in rendered
    assert "What should OpenSRE achieve?" in rendered
    assert "Example: Identify checkout 502s." in rendered
    assert "Add a goal condition to continue." not in rendered


@pytest.mark.parametrize("columns,rows", [(80, 30), (40, 12)])
def test_goal_set_form_shows_an_empty_condition_error_after_attempted_entry(
    columns: int, rows: int
) -> None:

    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    frames: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_goal_set_form({})
        app.input = pipe

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        def observe(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None:
                return
            rendered = "\n".join(
                "".join(screen.data_buffer[y][x].char for x in range(columns))
                for y in range(screen.height)
            )
            if not frames:
                frames.append(rendered)
                pipe.send_text("\r")
                return
            if "Add a goal condition to continue." in rendered:
                frames.append(rendered)
                pipe.send_text("\x03")

        def start() -> None:
            app.create_background_task(stop_if_stalled())

        app.after_render += observe
        result = app.run(pre_run=start)

    assert result is None
    assert "Add a goal condition to continue." not in frames[0]
    assert "Add a goal condition to continue." in frames[-1]


def test_goal_set_form_rejects_nonpositive_budget() -> None:
    with create_app_session(output=DummyOutput()):
        values = run_keys(build_goal_set_form({}), "Zero budget\r\t\r0\r\x01\x0b3\r\x13")
    assert values is not None
    assert values.condition == "Zero budget"
    assert values.max_turns == 3


def test_goal_set_cancel_leaves_existing_goal_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    console = Console(file=io.StringIO())
    assert goal_cmd._cmd_goal(session, console, ["set", "Old condition"])
    assert session.terminal.pending_prompt_default == "Old condition"

    def cancel(app: Any) -> Any:
        return run_keys(app, "Discarded draft\x03")

    monkeypatch.setattr(goal_cmd, "run_command_input", cancel)
    session.terminal.exclusive_stdin_active = True
    output = io.StringIO()
    with create_app_session(output=DummyOutput()):
        assert goal_cmd._cmd_goal(session, Console(file=output), ["set"]) is True
    assert session.session_goal is not None
    assert session.session_goal.condition == "Old condition"
    assert session.terminal.pending_prompt_default == "Old condition"
    assert session.terminal.pending_prompt_autosubmit is True


def test_complete_and_noninteractive_set_never_opens_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(_app: Any) -> None:
        raise AssertionError("must not open interactive input")

    monkeypatch.setattr(goal_cmd, "run_command_input", unexpected)
    with create_app_session(output=DummyOutput()):
        output, session = dispatch("/goal set --max-turns 3 Ship it")
    assert session.session_goal is not None
    assert session.session_goal.max_outer_turns == 3

    output, session = dispatch("/goal set", tty=False)
    assert "usage:" in output
    assert session.session_goal is None

    output, session = dispatch("/goal set --max-turns bogus", tty=False)
    assert "usage:" in output
    assert session.session_goal is None

    # An explicitly supplied empty budget never starts unbounded work, on TTY or not.
    for tty in (True, False):
        output, session = dispatch('/goal set --max-turns "" Ship it', tty=tty)
        assert "usage:" in output
        assert session.session_goal is None
        output, session = dispatch('/goal set --max-turns " " Ship it', tty=tty)
        assert "usage:" in output
        assert session.session_goal is None

    # Unknown flags keep the direct error path even on a TTY that owns stdin.
    fresh = Session()
    fresh.terminal.exclusive_stdin_active = True
    out_buf = io.StringIO()
    assert goal_cmd._cmd_goal(fresh, Console(file=out_buf), ["set", "--bogus"]) is True
    assert "unknown flag" in out_buf.getvalue()
    assert fresh.session_goal is None

    # Without stdin ownership the usage path runs instead of the form.
    idle = Session()
    out_buf = io.StringIO()
    assert goal_cmd._cmd_goal(idle, Console(file=out_buf), ["set"]) is True
    assert "usage:" in out_buf.getvalue()
    assert idle.session_goal is None


def test_goal_set_input_reserves_stdin() -> None:
    assert needs_command_input("/goal", ["set"])
    assert needs_command_input("/goal", ["set", "--max-turns", "3"])
    assert not needs_command_input("/goal", ["set", "--max-turns", "3", "Ship it"])
    assert not needs_command_input("/goal", ["set", "--bogus"])
    assert not needs_command_input("/goal", ["show"])
    assert not needs_command_input("/goal", [])
    assert input_policy.turn_needs_exclusive_stdin("/goal set", Session())


def test_goal_set_through_form_then_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    def collect(app: Any) -> Any:
        return run_keys(app, "Hold the line\r\x13")

    monkeypatch.setattr(goal_cmd, "run_command_input", collect)
    session = Session()
    session.terminal.exclusive_stdin_active = True
    buf = io.StringIO()
    console = Console(file=buf)
    with create_app_session(output=DummyOutput()):
        assert goal_cmd._cmd_goal(session, console, ["set"]) is True
        assert goal_cmd._cmd_goal(session, console, ["show"]) is True
        assert "Hold the line" in buf.getvalue()
        assert goal_cmd._cmd_goal(session, console, ["pause"]) is True
        assert goal_cmd._cmd_goal(session, console, ["resume"]) is True
        assert session.terminal.pending_prompt_default == "Hold the line"
        assert goal_cmd._cmd_goal(session, console, ["edit", "Hold the other line"]) is True
        assert session.session_goal is not None
        assert session.session_goal.condition == "Hold the other line"
        assert goal_cmd._cmd_goal(session, console, ["clear"]) is True
        assert session.session_goal is None


@pytest.mark.parametrize("columns,rows", [(80, 30), (40, 12)])
def test_goal_set_form_actions_visible_at_widths(
    columns: int, rows: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    captured: list[str] = []
    selected_rows: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_goal_set_form({})
        app.input = pipe

        def capture_summary(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or captured:
                return
            lines = [
                "".join(screen.data_buffer[y][x].char for x in range(columns))
                for y in range(screen.height)
            ]
            if not any("Set goal" in line for line in lines):
                return
            captured.extend(lines)
            cursor = screen.get_cursor_position(app.layout.current_window)
            selected_rows.append(lines[cursor.y])
            pipe.send_text("\x13")

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        def start() -> None:
            app.create_background_task(stop_if_stalled())
            pipe.send_text("Width check\r\t\t")

        app.after_render += capture_summary
        result = app.run(pre_run=start)

    assert captured, "Goal summary did not render before the timeout"
    assert "Set goal" in selected_rows[0]
    assert any("Cancel" in line for line in captured)
    assert any("Condition     Width check" in line for line in captured)
    assert any("Max turns" in line and "Unbounded" in line for line in captured)
    assert len(captured) <= rows
    assert result is not None and result.condition == "Width check"
    assert result.max_turns == 0


@pytest.mark.parametrize("tty", [True, False])
def test_cli_interactive_override_still_respects_noninteractive_dispatch(
    monkeypatch: pytest.MonkeyPatch, tty: bool
) -> None:
    monkeypatch.setenv(OPENSRE_INTERACTIVE_ENV, "0")

    def collect(app: Any) -> Any:
        assert tty, "Noninteractive dispatch must not open the form"
        return run_keys(app, "Override regression\r\x13")

    monkeypatch.setattr(goal_cmd, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        output, session = dispatch("/goal set", tty=tty)
    if tty:
        assert session.session_goal is not None
        assert session.session_goal.condition == "Override regression"
    else:
        assert "usage:" in output
        assert session.session_goal is None
