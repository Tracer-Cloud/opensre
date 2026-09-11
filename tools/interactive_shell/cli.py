"""Opensre CLI argv, planning, execution, and presenter-injected runner."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from infrastructure.scheduling.task_types import TaskKind
from tools.interactive_shell.shared import (
    ExecutionPolicyResult,
    ToolExecutionMode,
    ToolExecutionPlan,
)
from tools.interactive_shell.subprocess import (
    SHELL_COMMAND_TIMEOUT_SECONDS,
    SubprocessPresenter,
)
from tools.interactive_shell.working_directory import session_working_directory

# Rich style token names passed to presenter.print_command_output (resolved in surface).
_ERROR_STYLE = "error"

_PYTHON_EXECUTABLE_PREFIXES: tuple[str, ...] = ("python", "pypy")

OPENSRE_BLOCKED_SUBCOMMANDS: frozenset[str] = frozenset({"agent"})

INTERACTIVE_OPENSRE_COMMAND_PATHS: frozenset[str] = frozenset(
    {
        "onboard",
        "integrations setup",
    }
)

READ_ONLY_OPENSRE_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "health",
        "version",
        "list",
        "status",
        "show",
    }
)

# ``opensre integrations <sub>`` — probes and listings must not background, or
# the action turn ends on "started — task …" while verify still runs and the
# plan hangs on Invoking tools with "1 task running".
_READ_ONLY_INTEGRATIONS_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "list",
        "show",
        "verify",
        "status",
    }
)


class OpensreCommandClass(StrEnum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"


class OpensreExecutionMode(StrEnum):
    FOREGROUND = "foreground"
    FOREGROUND_STREAMING = "foreground_streaming"
    BACKGROUND = "background"


class OpensreRunOutcome(StrEnum):
    BLOCKED = "blocked"
    HANDED_OFF = "handed_off"
    EXECUTED_FOREGROUND = "executed_foreground"
    EXECUTED_BACKGROUND = "executed_background"
    DECLINED = "declined"
    INVALID = "invalid"


@dataclass(frozen=True)
class OpensreExecutionPlan:
    classification: OpensreCommandClass
    execution_mode: OpensreExecutionMode
    requires_confirmation: bool
    confirmation_reason: str | None


@dataclass(frozen=True)
class OpensreRunResult:
    outcome: OpensreRunOutcome
    attempted: bool
    display_command: str | None = None


@dataclass(frozen=True)
class ForegroundCliResult:
    """Outcome of a foreground opensre CLI subprocess."""

    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    start_failed: bool
    start_error: str | None = None


def _sys_executable_is_python() -> bool:
    return Path(sys.executable).name.lower().startswith(_PYTHON_EXECUTABLE_PREFIXES)


def _current_opensre_entrypoint() -> str | None:
    """Return the current ``opensre`` launcher when the REPL was started by one."""
    argv0 = sys.argv[0].strip() if sys.argv else ""
    if not argv0:
        return None
    if Path(argv0).name.lower() not in ("opensre", "opensre.exe"):
        return None
    if os.sep in argv0 or (os.altsep is not None and os.altsep in argv0):
        return str(Path(argv0).resolve())
    return argv0


def build_opensre_cli_argv(args: list[str]) -> list[str]:
    """Return argv for re-entering the OpenSRE Click CLI."""
    if entrypoint := _current_opensre_entrypoint():
        return [entrypoint, *args]
    if getattr(sys, "frozen", False) or not _sys_executable_is_python():
        return [sys.executable, *args]
    return [sys.executable, "-m", "surfaces.cli", *args]


def is_interactive_wizard(tokens: list[str]) -> bool:
    """True when ``tokens`` name an opensre subcommand that needs a full TTY wizard."""
    if not tokens:
        return False
    one = tokens[0].lower()
    if one in INTERACTIVE_OPENSRE_COMMAND_PATHS:
        return True
    if len(tokens) < 2:
        return False
    two = f"{one} {tokens[1].lower()}"
    return two in INTERACTIVE_OPENSRE_COMMAND_PATHS


def interactive_wizard_handoff_response_text(command_str: str) -> str:
    """Plain-text outcome for analytics when a wizard is redirected to a slash command."""
    return (
        f"`opensre {command_str}` is an interactive wizard that needs a full terminal. "
        f"Type /{command_str} directly in this shell to launch it."
    )


def _integrations_subcommand(tokens: list[str]) -> str | None:
    """Return the integrations subcommand, or ``None`` when not an integrations call."""
    if not tokens or tokens[0].lower() != "integrations":
        return None
    return tokens[1].lower() if len(tokens) > 1 else "list"


def _is_read_only_integrations(tokens: list[str]) -> bool:
    sub = _integrations_subcommand(tokens)
    return sub is not None and sub in _READ_ONLY_INTEGRATIONS_SUBCOMMANDS


def classify_opensre_command(tokens: list[str]) -> str:
    first_token = tokens[0].lower()
    if first_token in READ_ONLY_OPENSRE_SUBCOMMANDS:
        return OpensreCommandClass.READ_ONLY.value
    if _is_read_only_integrations(tokens):
        return OpensreCommandClass.READ_ONLY.value
    if first_token == "fleet":
        subcommand = tokens[1].lower() if len(tokens) > 1 else "list"
        if subcommand in {"list"}:
            return OpensreCommandClass.READ_ONLY.value
        if subcommand == "scan" and "--register" not in tokens[2:]:
            return OpensreCommandClass.READ_ONLY.value
    return OpensreCommandClass.MUTATING.value


def opensre_confirmation_reason(tokens: list[str]) -> str:
    if tokens[:2] == ["fleet", "scan"] and "--register" in tokens[2:]:
        return "register discovered local AI-agent processes"
    if tokens and tokens[0] == "fleet":
        return "this updates the local AI-agent registry"
    return "this opensre subcommand may change local config or infrastructure"


def build_opensre_execution_plan(tokens: list[str]) -> OpensreExecutionPlan:
    """Compute classification + execution mode from one canonical policy table."""
    classification = OpensreCommandClass(classify_opensre_command(tokens))
    first_token = tokens[0].lower()

    execution_mode = OpensreExecutionMode.BACKGROUND
    if first_token in READ_ONLY_OPENSRE_SUBCOMMANDS:
        execution_mode = OpensreExecutionMode.FOREGROUND
    elif _is_read_only_integrations(tokens):
        # Wait for verify/list/show so the tool observation includes pass/fail
        # and the plan can leave Invoking tools instead of parking on a task id.
        execution_mode = OpensreExecutionMode.FOREGROUND
    elif first_token == "fleet":
        subcommand = tokens[1].lower() if len(tokens) > 1 else "list"
        if subcommand == "watch":
            execution_mode = OpensreExecutionMode.FOREGROUND_STREAMING
        elif subcommand in {"list", "register", "forget", "scan"}:
            execution_mode = OpensreExecutionMode.FOREGROUND

    requires_confirmation = classification is OpensreCommandClass.MUTATING
    reason = (
        opensre_confirmation_reason([token.lower() for token in tokens])
        if requires_confirmation
        else None
    )
    return OpensreExecutionPlan(
        classification=classification,
        execution_mode=execution_mode,
        requires_confirmation=requires_confirmation,
        confirmation_reason=reason,
    )


def to_tool_execution_plan(plan: OpensreExecutionPlan) -> ToolExecutionPlan:
    mode = ToolExecutionMode.BACKGROUND
    if plan.execution_mode is OpensreExecutionMode.FOREGROUND:
        mode = ToolExecutionMode.FOREGROUND
    elif plan.execution_mode is OpensreExecutionMode.FOREGROUND_STREAMING:
        mode = ToolExecutionMode.FOREGROUND_STREAMING
    if not plan.requires_confirmation:
        policy = ExecutionPolicyResult(
            verdict="allow",
            tool_type="cli_command",
            reason=None,
            hint=None,
            shell_classification=plan.classification.value,
        )
    else:
        policy = ExecutionPolicyResult(
            verdict="ask",
            tool_type="cli_command",
            reason=plan.confirmation_reason,
            hint="Use a read-only subcommand (health, version, integrations verify/list/show)",
            shell_classification=plan.classification.value,
        )
    return ToolExecutionPlan(
        tool_type="cli_command",
        classification=plan.classification.value,
        execution_mode=mode,
        policy=policy,
    )


def run_foreground_cli(
    argv_list: list[str],
    *,
    cwd: str | None = None,
    timeout_seconds: int = SHELL_COMMAND_TIMEOUT_SECONDS,
) -> ForegroundCliResult:
    try:
        completed = subprocess.run(
            argv_list,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ForegroundCliResult(
            stdout=str(exc.output or ""),
            stderr=str(exc.stderr or ""),
            exit_code=None,
            timed_out=True,
            start_failed=False,
        )
    except Exception as exc:  # noqa: BLE001
        return ForegroundCliResult(
            stdout="",
            stderr="",
            exit_code=None,
            timed_out=False,
            start_failed=True,
            start_error=str(exc),
        )
    return ForegroundCliResult(
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        exit_code=completed.returncode,
        timed_out=False,
        start_failed=False,
    )


def spawn_streaming_cli(
    argv_list: list[str],
    *,
    cwd: str | None = None,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        argv_list,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )


def _print_wizard_handoff(presenter: SubprocessPresenter, command_str: str) -> None:
    presenter.print(
        f"[warning]`opensre {command_str}` is an interactive wizard that needs a full terminal.[/]"
    )
    presenter.print(
        f"[dim]Type [bold]/{command_str}[/bold] directly in this shell to launch it.[/]"
    )


def _run_foreground_via_presenter(
    presenter: SubprocessPresenter,
    *,
    argv_list: list[str],
    display_command: str,
) -> None:
    presenter.print_bold_command(display_command)
    result = run_foreground_cli(
        argv_list,
        cwd=session_working_directory(presenter.session),
        timeout_seconds=SHELL_COMMAND_TIMEOUT_SECONDS,
    )
    if result.start_failed:
        if result.start_error:
            presenter.report_exception(
                RuntimeError(result.start_error),
                context="surfaces.interactive_shell.opensre_cli.start",
            )
            presenter.print_error(f"failed to start: {result.start_error}")
        presenter.session.record("cli_command", display_command, ok=False)
        return
    presenter.print_command_output(result.stdout)
    presenter.print_command_output(result.stderr, style=_ERROR_STYLE)
    if result.timed_out:
        presenter.print(
            f"[error]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/]"
        )
        presenter.session.record("cli_command", display_command, ok=False)
        return
    ok = result.exit_code == 0
    if not ok:
        presenter.print(f"[error]command failed (exit {result.exit_code}):[/]")
    presenter.session.record("cli_command", display_command, ok=ok)


def _run_streaming_via_presenter(
    presenter: SubprocessPresenter,
    *,
    argv_list: list[str],
    display_command: str,
) -> None:
    presenter.print_bold_command(display_command)
    try:
        proc = spawn_streaming_cli(
            argv_list,
            cwd=session_working_directory(presenter.session),
        )
    except Exception as exc:  # noqa: BLE001
        presenter.report_exception(
            exc,
            context="surfaces.interactive_shell.opensre_cli.start",
        )
        presenter.print_error(f"failed to start: {exc}")
        presenter.session.record("cli_command", display_command, ok=False)
        return
    if proc.stdout is not None:
        for line in proc.stdout:
            presenter.print_command_output(line)
    code = proc.wait()
    ok = code == 0
    if not ok:
        presenter.print(f"[error]command failed (exit {code}):[/]")
    presenter.session.record("cli_command", display_command, ok=ok)


def run_opensre_cli_command_result(
    args: str,
    presenter: SubprocessPresenter,
) -> OpensreRunResult:
    """Run an opensre subcommand (not agent) via the injected presenter."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    if not tokens:
        return OpensreRunResult(outcome=OpensreRunOutcome.INVALID, attempted=False)

    first_token = tokens[0].lower()
    if first_token in OPENSRE_BLOCKED_SUBCOMMANDS:
        presenter.print(f"[error]Cannot run `opensre {first_token}`: subcommand is blocked.[/]")
        return OpensreRunResult(outcome=OpensreRunOutcome.BLOCKED, attempted=False)

    if is_interactive_wizard(tokens):
        command_str = " ".join(tokens)
        _print_wizard_handoff(presenter, command_str)
        presenter.session.record(
            "cli_command",
            f"opensre {command_str}",
            ok=False,
            response_text=interactive_wizard_handoff_response_text(command_str),
        )
        return OpensreRunResult(
            outcome=OpensreRunOutcome.HANDED_OFF,
            attempted=True,
            display_command=f"opensre {command_str}",
        )

    plan = build_opensre_execution_plan(tokens)
    execution_plan = to_tool_execution_plan(plan)
    display_command = f"opensre {' '.join(tokens)}"

    if not presenter.execution_allowed(
        execution_plan.policy,
        action_summary=f"$ {display_command}",
    ):
        presenter.session.record("cli_command", display_command, ok=False)
        return OpensreRunResult(
            outcome=OpensreRunOutcome.DECLINED,
            attempted=True,
            display_command=display_command,
        )

    argv_list = build_opensre_cli_argv(tokens)
    if execution_plan.execution_mode in {
        ToolExecutionMode.FOREGROUND,
        ToolExecutionMode.FOREGROUND_STREAMING,
    }:
        if execution_plan.execution_mode is ToolExecutionMode.FOREGROUND_STREAMING:
            _run_streaming_via_presenter(
                presenter,
                argv_list=argv_list,
                display_command=display_command,
            )
        else:
            _run_foreground_via_presenter(
                presenter,
                argv_list=argv_list,
                display_command=display_command,
            )
        return OpensreRunResult(
            outcome=OpensreRunOutcome.EXECUTED_FOREGROUND,
            attempted=True,
            display_command=display_command,
        )

    presenter.session.record("cli_command", display_command)
    presenter.start_background_cli_task(
        display_command=display_command,
        argv_list=argv_list,
        timeout_seconds=SHELL_COMMAND_TIMEOUT_SECONDS,
        kind=TaskKind.CLI_COMMAND,
    )
    return OpensreRunResult(
        outcome=OpensreRunOutcome.EXECUTED_BACKGROUND,
        attempted=True,
        display_command=display_command,
    )


def run_opensre_cli_command(args: str, presenter: SubprocessPresenter) -> bool:
    result = run_opensre_cli_command_result(args, presenter)
    return result.attempted


__all__ = [
    "ForegroundCliResult",
    "INTERACTIVE_OPENSRE_COMMAND_PATHS",
    "OPENSRE_BLOCKED_SUBCOMMANDS",
    "READ_ONLY_OPENSRE_SUBCOMMANDS",
    "OpensreCommandClass",
    "OpensreExecutionMode",
    "OpensreExecutionPlan",
    "OpensreRunOutcome",
    "OpensreRunResult",
    "_run_foreground_via_presenter",
    "_run_streaming_via_presenter",
    "build_opensre_cli_argv",
    "build_opensre_execution_plan",
    "classify_opensre_command",
    "interactive_wizard_handoff_response_text",
    "is_interactive_wizard",
    "opensre_confirmation_reason",
    "run_foreground_cli",
    "run_opensre_cli_command",
    "run_opensre_cli_command_result",
    "spawn_streaming_cli",
    "to_tool_execution_plan",
]
