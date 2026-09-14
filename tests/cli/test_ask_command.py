from __future__ import annotations

import contextlib
import json
import signal

from click.testing import CliRunner

from surfaces.cli.app import cli
from surfaces.cli.ask.service import (
    AskError,
    AskExitCode,
    AskOutcome,
    AskSignal,
    AskStatus,
)
from surfaces.cli.commands.ask import _echo_answer, ask_command


def _success(response: str = "done") -> AskOutcome:
    return AskOutcome(status=AskStatus.SUCCESS, response=response)


def test_ask_answer_stays_plain_when_piped(monkeypatch, capsys) -> None:
    # Arrange: stdout is not a terminal (piped into a script / another command).
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    # Act
    _echo_answer("Branch is **main**.")

    # Assert: raw Markdown is preserved so a consuming script can parse it.
    assert "**main**" in capsys.readouterr().out


def test_ask_answer_renders_markdown_on_a_tty(monkeypatch) -> None:
    # Arrange: stdout is an interactive terminal. Use a spy console so the path
    # is verified without constructing a real Rich console (which would cache
    # global terminal/color state and bleed into other tests).
    from rich.markdown import Markdown

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    printed: list[object] = []

    class _SpyConsole:
        def use_theme(self, *_args, **_kwargs):
            return contextlib.nullcontext()

        def print(self, renderable: object = "") -> None:
            printed.append(renderable)

    monkeypatch.setattr("rich.console.Console", lambda *_a, **_k: _SpyConsole())

    # Act
    _echo_answer("Branch is **main**.")

    # Assert: the answer is handed to Markdown rendering, not echoed as raw text.
    assert len(printed) == 1
    assert isinstance(printed[0], Markdown)


def test_ask_passes_prompt_and_invocation_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", fake_run)

    result = CliRunner().invoke(
        ask_command,
        ["check latency", "--allowed-tool", "grafana_query"],
    )

    assert result.exit_code == 0
    assert result.output == "done\n"
    assert captured == {
        "prompt": "check latency",
        "allowed_tools": ("grafana_query",),
        "bypass_approvals": False,
        "tool_event_observer": None,
    }


def test_ask_passes_a_live_observer_only_for_an_interactive_text_terminal(monkeypatch) -> None:
    captured: dict[str, object] = {}
    enabled_values: list[bool] = []
    observer = object()

    @contextlib.contextmanager
    def fake_progress_scope(*, enabled: bool):
        enabled_values.append(enabled)
        yield observer

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.ask.progress.ask_progress_scope", fake_progress_scope)
    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", fake_run)
    monkeypatch.setattr("surfaces.cli.commands.ask._show_live_progress", lambda: True)

    result = CliRunner().invoke(ask_command, ["check latency"])

    assert result.exit_code == 0
    assert enabled_values == [True]
    assert captured["tool_event_observer"] is observer


def test_live_progress_is_disabled_for_json_or_redirected_output(monkeypatch) -> None:
    from surfaces.cli.commands.ask import _show_live_progress

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.commands.ask.is_json_output", lambda: False)
    assert _show_live_progress() is True

    monkeypatch.setattr("surfaces.cli.commands.ask.is_json_output", lambda: True)
    assert _show_live_progress() is False

    monkeypatch.setattr("surfaces.cli.commands.ask.is_json_output", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert _show_live_progress() is False


def test_root_yes_does_not_bypass_ask_approvals(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", fake_run)

    result = CliRunner().invoke(cli, ["-y", "ask", "check latency"])

    assert result.exit_code == 0
    assert captured["bypass_approvals"] is False


def test_ask_reads_prompt_from_stdin(monkeypatch) -> None:
    seen: list[str] = []

    def fake_run(prompt: str, **_kwargs: object) -> AskOutcome:
        seen.append(prompt)
        return _success()

    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", fake_run)

    result = CliRunner().invoke(ask_command, ["-"], input="from stdin\n")

    assert result.exit_code == 0
    assert seen == ["from stdin"]


def test_ask_interrupt_while_reading_stdin_returns_signal_exit(monkeypatch) -> None:
    def interrupt(_value: str) -> str:
        raise AskSignal(signal.SIGINT)

    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.commands.ask._resolve_prompt", interrupt)
    monkeypatch.setattr("surfaces.cli.commands.ask.is_json_output", lambda: True)

    result = CliRunner().invoke(ask_command, ["-"])

    assert result.exit_code == 130
    assert json.loads(result.output)["status"] == "cancelled"


def test_ask_rejects_empty_prompt(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())

    result = CliRunner().invoke(ask_command, ["-"], input="  \n")

    assert result.exit_code == 2
    assert "PROMPT must not be empty" in result.output


def test_ask_rejects_conflicting_approval_options(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())

    result = CliRunner().invoke(
        ask_command,
        ["prompt", "--allowed-tool", "one", "--dangerously-bypass-approvals"],
    )

    assert result.exit_code == 2
    assert "cannot be combined" in result.output


def test_ask_rejects_unknown_allowed_tool_before_execution(monkeypatch) -> None:
    called = False

    def fake_run(*_args: object, **_kwargs: object) -> AskOutcome:
        nonlocal called
        called = True
        return _success()

    monkeypatch.setattr(
        "surfaces.cli.ask.approval.unknown_allowed_tools",
        lambda _v: ("typo",),
    )
    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", fake_run)

    result = CliRunner().invoke(ask_command, ["prompt", "--allowed-tool", "typo"])

    assert result.exit_code == 2
    assert "unknown registered tool name(s): typo" in result.output
    assert called is False


def test_ask_json_output_is_one_stable_document(monkeypatch) -> None:
    outcome = AskOutcome(
        status=AskStatus.ERROR,
        response="",
        error=AskError(message="failed", suggestion="retry"),
        exit_code=AskExitCode.ERROR,
    )
    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", lambda *_a, **_kw: outcome)
    monkeypatch.setattr("surfaces.cli.commands.ask.is_json_output", lambda: True)

    result = CliRunner().invoke(ask_command, ["prompt"])

    assert result.exit_code == AskExitCode.ERROR
    assert json.loads(result.output) == {
        "status": "error",
        "response": "",
        "denied_tools": [],
        "error": {"message": "failed", "suggestion": "retry"},
    }
    assert result.stderr == ""
    assert result.output.count("\n") == 1
