"""Tests for the agent slash-command tool.

Focus: agent-planned interactive picker/wizard commands must be deferred to the
REPL loop's exclusive-stdin path rather than run inline, where they would race
the live ``prompt_async()`` and leak terminal CPR replies (``ESC[row;colR``)
into the input line.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import pytest
from rich.console import Console

import tools.interactive_shell.actions.slash as slash_tool
from core.agent_harness.tools.tool_context import ActionToolScope
from surfaces.interactive_shell.session import Session


@dataclass
class FakeSlashPorts:
    """Controllable slash runtime adapter used by action-tool tests."""

    tty: bool = True
    dispatch_result: bool = True
    dispatched: list[str] = field(default_factory=list)

    def command_exists(self, _name: str) -> bool:
        return True

    def command_is_mutating(self, _name: str) -> bool:
        return True

    def tty_interactive(self) -> bool:
        return self.tty

    def format_turn_outcome(self, command: str, *, ok: bool) -> str:
        status = "succeeded" if ok else "failed"
        return f"slash {command} ({status})"

    def execution_allowed(
        self,
        *,
        policy: Any,
        **_kwargs: Any,
    ) -> bool:
        del policy
        return True

    def dispatch(
        self,
        command: str,
        **_kwargs: Any,
    ) -> bool:
        self.dispatched.append(command)
        return self.dispatch_result


def _ctx(
    *,
    ports: FakeSlashPorts | None = None,
    request_exit: Any = None,
) -> tuple[ActionToolScope, io.StringIO, Session, FakeSlashPorts]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    session = Session()
    resolved_ports = ports or FakeSlashPorts()

    return (
        ActionToolScope(
            session=session,
            console=console,
            request_exit=request_exit,
            slash_ports=resolved_ports,
        ),
        buf,
        session,
        resolved_ports,
    )


@pytest.mark.parametrize(
    ("command", "args", "expected"),
    [
        ("/integrations", ["remove", "github"], "/integrations remove github"),
        ("/integrations", ["setup", "datadog"], "/integrations setup datadog"),
        ("/mcp", ["connect", "github"], "/mcp connect github"),
        ("/mcp", ["disconnect", "github"], "/mcp disconnect github"),
        ("/integrations", [], "/integrations"),
        ("/mcp", [], "/mcp"),
        ("/loops", ["show"], "/loops show"),
    ],
)
def test_interactive_picker_command_is_deferred_to_exclusive_stdin(
    command: str,
    args: list[str],
    expected: str,
) -> None:
    """Picker commands are queued until the REPL owns exclusive stdin."""
    ctx, buf, session, ports = _ctx(ports=FakeSlashPorts(tty=True))

    handled = slash_tool.execute_slash_tool(
        {"command": command, "args": args},
        ctx,
    )

    assert handled is True
    assert ports.dispatched == []
    assert session.terminal.pending_prompt_default == expected
    assert session.terminal.pending_prompt_autosubmit is True
    assert session.history == []
    # Nothing is printed: the prefilled prompt line is the only announcement,
    # so the command is not shown twice before it runs.
    assert buf.getvalue() == ""


def test_interactive_picker_runs_inline_when_exclusive_stdin_active() -> None:
    """An already-exclusive turn must dispatch inline instead of re-queueing."""
    ctx, buf, session, ports = _ctx(ports=FakeSlashPorts(tty=True))
    session.terminal.exclusive_stdin_active = True

    handled = slash_tool.execute_slash_tool(
        {"command": "/integrations", "args": []},
        ctx,
    )

    assert handled is True
    assert ports.dispatched == ["/integrations"]
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False
    # Exclusive stdin means the user typed this slash literally, so the prompt
    # line already shows it — announcing it again would be the third rendering
    # of one command.
    assert buf.getvalue() == ""


def test_agent_resolved_slash_announces_itself() -> None:
    """Free text resolved into a slash has no prompt echo, so it must announce."""
    ctx, buf, session, ports = _ctx(ports=FakeSlashPorts(tty=True))

    slash_tool.execute_slash_tool({"command": "/health", "args": []}, ctx)

    assert ports.dispatched == ["/health"]
    assert "$ /health" in buf.getvalue()


def test_goal_set_banner_does_not_quote_harmless_punctuation() -> None:
    """``days?`` / ``PostHog;`` must stay readable on the ``$`` line."""
    ctx, buf, _session, ports = _ctx(ports=FakeSlashPorts(tty=True))
    args = [
        "set",
        "--max-turns",
        "4",
        "What",
        "is",
        "D7",
        "retention",
        "for",
        "users",
        "who",
        "signed",
        "up",
        "on",
        "Windows",
        "in",
        "the",
        "last",
        "30",
        "days?",
        "Prefer",
        "live",
        "PostHog;",
        "otherwise",
        "draft",
        "HogQL",
    ]

    slash_tool.execute_slash_tool({"command": "/goal", "args": args}, ctx)

    line = ports.dispatched[0]
    assert "'days?'" not in line
    assert "'PostHog;'" not in line
    assert "days?" in line
    assert "PostHog;" in line
    assert "days?" in buf.getvalue()
    assert "'days?'" not in buf.getvalue()


def test_cron_expression_stays_one_argument_after_join() -> None:
    """Spaced cron fields must remain one token for ``dispatch_slash``."""
    ctx, _buf, _session, ports = _ctx(ports=FakeSlashPorts(tty=True))

    slash_tool.execute_slash_tool(
        {
            "command": "/cron",
            "args": ["add", "--cron", "0 8 * * 1-5", "--prompt", "triage"],
        },
        ctx,
    )

    assert ports.dispatched == ["/cron add --cron '0 8 * * 1-5' --prompt triage"]


def test_interactive_picker_runs_inline_when_not_a_tty() -> None:
    """Without an interactive TTY there is no live prompt to race."""
    ctx, _buf, session, ports = _ctx(ports=FakeSlashPorts(tty=False))

    slash_tool.execute_slash_tool(
        {
            "command": "/integrations",
            "args": ["remove", "github"],
        },
        ctx,
    )

    assert ports.dispatched == ["/integrations remove github"]
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False


def test_duplicate_slash_invoke_alone_may_run_twice() -> None:
    """Slash tool itself does not suppress repeats; the action-turn guard does."""
    ctx, _buf, _session, ports = _ctx(ports=FakeSlashPorts(tty=True))
    args = {"command": "/integrations", "args": ["list"]}

    assert slash_tool.execute_slash_tool(args, ctx) is True
    assert slash_tool.execute_slash_tool(args, ctx) is True

    assert ports.dispatched == ["/integrations list", "/integrations list"]


def test_interleaved_slash_invoke_runs_each_time() -> None:
    """A → B → A must all dispatch (Greptile: shared-set suppress was wrong)."""
    ctx, _buf, _session, ports = _ctx(ports=FakeSlashPorts(tty=True))

    assert slash_tool.execute_slash_tool({"command": "/health", "args": []}, ctx) is True
    assert (
        slash_tool.execute_slash_tool({"command": "/integrations", "args": ["list"]}, ctx) is True
    )
    assert slash_tool.execute_slash_tool({"command": "/health", "args": []}, ctx) is True

    assert ports.dispatched == ["/health", "/integrations list", "/health"]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("/integrations", ["list"]),
        ("/integrations", ["show", "github"]),
        ("/loops", ["show", "abc123"]),
        ("/health", []),
    ],
)
def test_non_picker_slash_commands_run_inline_even_in_a_tty(
    command: str,
    args: list[str],
) -> None:
    """Commands that do not read raw stdin continue to run inline."""
    ctx, _buf, session, ports = _ctx(ports=FakeSlashPorts(tty=True))

    slash_tool.execute_slash_tool(
        {"command": command, "args": args},
        ctx,
    )

    expected = " ".join([command, *args]) if args else command
    assert ports.dispatched == [expected]
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False


def test_exit_slash_requests_runtime_exit() -> None:
    ports = FakeSlashPorts(dispatch_result=False)
    requested_exit: list[bool] = []

    ctx, _buf, _session, _ports = _ctx(
        ports=ports,
        request_exit=lambda: requested_exit.append(True),
    )

    handled = slash_tool.execute_slash_tool(
        {"command": "/quit", "args": []},
        ctx,
    )

    assert handled is True
    assert ports.dispatched == ["/quit"]
    assert requested_exit == [True]


@dataclass
class FailureRecordingSlashPorts(FakeSlashPorts):
    """Fake ports whose dispatch records a failing slash row, like a real
    handler whose delegated CLI subprocess exited non-zero."""

    error_text: str = "Usage: opensre cron remove [OPTIONS] TASK_ID"
    fail_commands: frozenset[str] = frozenset({"/cron remove"})

    def dispatch(self, command: str, *, session: Any = None, **_kwargs: Any) -> bool:
        self.dispatched.append(command)
        failed = command in self.fail_commands
        session.record(
            "slash",
            command,
            ok=not failed,
            response_text=self.error_text if failed else None,
        )
        return True


def test_failed_dispatch_returns_error_observation() -> None:
    """A failed slash row must reach the model, not collapse into ok=true."""
    ports = FailureRecordingSlashPorts(tty=True)
    ctx, _buf, _session, _ports = _ctx(ports=ports)

    result = slash_tool.execute_slash_tool({"command": "/cron", "args": ["remove"]}, ctx)

    assert result == {
        "ok": False,
        "command": "/cron remove",
        "error": "Usage: opensre cron remove [OPTIONS] TASK_ID",
    }


def test_corrected_retry_after_failure_executes_in_same_turn() -> None:
    """The per-turn dedupe blocks identical re-runs only, not a corrected line."""
    ports = FailureRecordingSlashPorts(tty=True)
    ctx, _buf, _session, _ports = _ctx(ports=ports)

    first = slash_tool.execute_slash_tool({"command": "/cron", "args": ["remove"]}, ctx)
    retry = slash_tool.execute_slash_tool(
        {"command": "/cron", "args": ["remove", "ecf7c2580b83"]}, ctx
    )

    assert isinstance(first, dict) and first["ok"] is False
    assert retry is True
    assert ports.dispatched == ["/cron remove", "/cron remove ecf7c2580b83"]


def test_error_observation_excerpt_is_capped() -> None:
    ports = FailureRecordingSlashPorts(tty=True, error_text="x" * 5000)
    ctx, _buf, _session, _ports = _ctx(ports=ports)

    result = slash_tool.execute_slash_tool({"command": "/cron", "args": ["remove"]}, ctx)

    assert isinstance(result, dict)
    # Matches _MAX_OBSERVED_ERROR_CHARS in tools.interactive_shell.actions.slash.
    assert len(result["error"]) == 700


def test_failed_rows_from_earlier_turns_are_not_evidence() -> None:
    """Only rows THIS dispatch appended count; stale failures must not leak in."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    session = Session()
    session.record("slash", "/health", ok=False, response_text="old failure")
    ports = FakeSlashPorts(tty=True)
    ctx = ActionToolScope(
        session=session,
        console=console,
        slash_ports=ports,
        history_start=len(session.history),
    )

    result = slash_tool.execute_slash_tool({"command": "/health", "args": []}, ctx)

    assert result is True
    assert ports.dispatched == ["/health"]


def test_cron_list_output_reaches_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful '/cron list' must surface task ids in the observation so the
    agent can chain a data-dependent '/cron remove <id>' in the same turn."""
    import subprocess

    import surfaces.interactive_shell.command_registry.cli_parity as cli_parity
    from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("capture_output") is True
        return subprocess.CompletedProcess(
            cmd,
            returncode=0,
            stdout="ecf7c2580b83  daily_summary  0 8 * * 1-5  UTC  slack",
            stderr="",
        )

    monkeypatch.setattr(cli_parity.subprocess, "run", _fake_run)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    session = Session()
    ctx = ActionToolScope(
        session=session,
        console=console,
        is_tty=True,
        slash_ports=repl_slash_ports(),
    )

    result = slash_tool.execute_slash_tool({"command": "/cron", "args": ["list"]}, ctx)

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert "ecf7c2580b83" in result["output"]


def test_cron_remove_missing_task_id_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end through the real dispatcher: '/cron remove' without a TASK_ID
    exits 2; the tool observation must say so instead of {"ok": true}."""
    import subprocess

    import surfaces.interactive_shell.command_registry.cli_parity as cli_parity
    from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports

    def _fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            returncode=2,
            stdout="",
            stderr="Usage: opensre cron remove [OPTIONS] TASK_ID",
        )

    monkeypatch.setattr(cli_parity.subprocess, "run", _fake_run)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    session = Session()
    ctx = ActionToolScope(
        session=session,
        console=console,
        is_tty=True,
        slash_ports=repl_slash_ports(),
    )

    result = slash_tool.execute_slash_tool({"command": "/cron", "args": ["remove"]}, ctx)

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["command"] == "/cron remove"
    # Captured Click usage text tells the model exactly what argument it forgot.
    assert "Usage: opensre cron remove [OPTIONS] TASK_ID" in result["error"]
    latest = [row for row in session.history if row.get("type") == "slash"][-1]
    assert latest["ok"] is False


def test_non_mutating_slash_command_skips_the_execution_gate() -> None:
    # A control command (mutating=False) must dispatch without the execution
    # gate, so a standing plan-only request cannot block /exit.
    class _NonMutatingPorts(FakeSlashPorts):
        def command_is_mutating(self, _name: str) -> bool:
            return False

        def execution_allowed(self, **_kwargs: Any) -> bool:
            raise AssertionError("the gate must not run for a non-mutating command")

    ctx, _buf, _session, ports = _ctx(ports=_NonMutatingPorts())

    slash_tool.execute_slash_tool({"command": "/exit"}, ctx)

    assert ports.dispatched == ["/exit"]
