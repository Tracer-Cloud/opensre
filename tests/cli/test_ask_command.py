from __future__ import annotations

import contextlib
import json
import shlex
import signal

from click.testing import CliRunner

from surfaces.cli.app import cli
from surfaces.cli.ask.service import (
    AskError,
    AskExitCode,
    AskOutcome,
    AskQuestion,
    AskSignal,
    AskStatus,
)
from surfaces.cli.commands.ask import _echo_answer, _render_outcome, ask_command


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


def test_required_choice_prints_exact_resume_command_on_stderr(monkeypatch, capsys) -> None:
    outcome = AskOutcome(
        status=AskStatus.NEEDS_INPUT,
        response="Which environment?\n  1. Production\n  2. Staging",
        session_id="session-123",
        questions=(AskQuestion("Environment", "Which environment?", ("Production", "Staging")),),
        exit_code=AskExitCode.NEEDS_INPUT,
    )
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.commands.ask._echo_answer", lambda _text: None)

    _render_outcome(outcome)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Session: session-123" in captured.err
    assert 'opensre ask --resume session-123 "1"' in captured.err


def test_required_choice_prints_session_id_when_stderr_is_redirected(
    monkeypatch,
    capsys,
) -> None:
    outcome = AskOutcome(
        status=AskStatus.NEEDS_INPUT,
        response="Which environment?\n  1. Production",
        session_id="session-123",
        questions=(AskQuestion("Environment", "Which environment?", ("Production",)),),
        exit_code=AskExitCode.NEEDS_INPUT,
    )
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    monkeypatch.setattr("surfaces.cli.commands.ask._echo_answer", lambda _text: None)

    _render_outcome(outcome)

    captured = capsys.readouterr()
    assert captured.err == "Session: session-123\n"
    assert captured.out == ""


def test_batch_resume_command_is_shell_safe_and_uses_unambiguous_keys(
    monkeypatch,
    capsys,
) -> None:
    outcome = AskOutcome(
        status=AskStatus.NEEDS_INPUT,
        response="Choose values",
        session_id="session-123",
        questions=(
            AskQuestion("What's affected", "Which service?", ("API", "Worker")),
            AskQuestion("What's affected", "Which region?", ("India", "US")),
        ),
        exit_code=AskExitCode.NEEDS_INPUT,
    )
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.commands.ask._echo_answer", lambda _text: None)

    _render_outcome(outcome)

    continue_line = next(
        line for line in capsys.readouterr().err.splitlines() if line.startswith("Continue: ")
    )
    argv = shlex.split(continue_line.removeprefix("Continue: "))
    assert argv[:4] == ["opensre", "ask", "--resume", "session-123"]
    assert json.loads(argv[4]) == {"1": "1", "2": "1"}


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
        "resume_session_id": None,
        "ephemeral": False,
        "context_files": (),
    }


def test_ask_passes_repeatable_context_files(monkeypatch, tmp_path) -> None:
    alert = tmp_path / "alert.json"
    alert.write_text('{"service":"checkout"}', encoding="utf-8")
    deployment = tmp_path / "deployment.md"
    deployment.write_text("Deployed release 42", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", fake_run)

    result = CliRunner().invoke(
        ask_command,
        [
            "-i",
            str(alert),
            "--context-file",
            str(deployment),
            "investigate checkout",
        ],
    )

    assert result.exit_code == 0
    context_files = captured["context_files"]
    assert isinstance(context_files, tuple)
    assert [item.path for item in context_files] == [str(alert), str(deployment)]
    assert [item.content for item in context_files] == [
        '{"service":"checkout"}',
        "Deployed release 42",
    ]


def test_ask_rejects_invalid_context_before_agent_start(monkeypatch, tmp_path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("\n", encoding="utf-8")
    called = False

    def fake_run(*_args: object, **_kwargs: object) -> AskOutcome:
        nonlocal called
        called = True
        return _success()

    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", fake_run)

    result = CliRunner().invoke(ask_command, ["-i", str(empty), "investigate"])

    assert result.exit_code == 2
    assert "must not be empty" in result.output
    assert called is False


def test_ask_passes_resume_and_ephemeral_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", fake_run)

    resumed = CliRunner().invoke(ask_command, ["--resume", "abc123", "continue"])
    assert resumed.exit_code == 0
    assert captured["resume_session_id"] == "abc123"
    assert captured["ephemeral"] is False

    ephemeral = CliRunner().invoke(ask_command, ["--ephemeral", "one shot"])
    assert ephemeral.exit_code == 0
    assert captured["resume_session_id"] is None
    assert captured["ephemeral"] is True


def test_ask_rejects_resume_with_ephemeral() -> None:
    result = CliRunner().invoke(ask_command, ["--resume", "abc123", "--ephemeral", "reply"])

    assert result.exit_code == 2
    assert "--resume cannot be combined with --ephemeral" in result.output


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
        "session_id": None,
        "questions": [],
        "error": {"message": "failed", "suggestion": "retry"},
    }
    assert result.stderr == ""
    assert result.output.count("\n") == 1


def test_ask_json_required_choice_includes_resume_fields(monkeypatch) -> None:
    outcome = AskOutcome(
        status=AskStatus.NEEDS_INPUT,
        response="Which environment?\n  1. Production\n  2. Staging",
        session_id="session-123",
        questions=(AskQuestion("Environment", "Which environment?", ("Production", "Staging")),),
        exit_code=AskExitCode.NEEDS_INPUT,
    )
    monkeypatch.setattr("surfaces.cli.ask.approval.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.ask.service.run_ask", lambda *_a, **_kw: outcome)
    monkeypatch.setattr("surfaces.cli.commands.ask.is_json_output", lambda: True)

    result = CliRunner().invoke(ask_command, ["deploy"])

    assert result.exit_code == AskExitCode.NEEDS_INPUT
    payload = json.loads(result.output)
    assert payload["status"] == "needs_input"
    assert payload["session_id"] == "session-123"
    assert payload["questions"] == [
        {
            "label": "Environment",
            "title": "Which environment?",
            "options": ["Production", "Staging"],
            "multi_select": False,
            "allow_custom": True,
        }
    ]
    assert result.stderr == ""
