"""Claude Code implementation executor.

Spawns the Claude Code CLI to implement a requested change in the current
repository, applies the execution policy, tracks the launch as a background
task, and watches the subprocess lifecycle in a daemon thread.

Lives next to the agent-facing ``tools.interactive_shell.actions.implementation``.
``subprocess`` and ``threading`` are referenced as module globals so tests can
patch ``tools.interactive_shell.implementation.claude_code_executor.subprocess.Popen`` /
``.threading.Thread``; ``ClaudeCodeAdapter`` is likewise a module global that
tests patch directly.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from infrastructure.scheduling.task_types import TaskKind
from integrations.llm_cli import ClaudeCodeAdapter, build_cli_subprocess_env
from tools.interactive_shell.shared import allow_tool
from tools.interactive_shell.subprocess import (
    CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS,
    MAX_COMMAND_OUTPUT_CHARS,
    TASK_DIAG_CHARS,
    SubprocessPresenter,
    terminate_child_process,
)
from tools.interactive_shell.working_directory import session_working_directory

_DIM_STYLE = "dim"
_ERROR_STYLE = "error"
_WARNING_STYLE = "warning"

_IMPLEMENT_PERMISSION_MODE_ENV = "CLAUDE_CODE_IMPLEMENT_PERMISSION_MODE"
_DEFAULT_IMPLEMENT_PERMISSION_MODE = "acceptEdits"


class ClaudeInvocation(Protocol):
    @property
    def argv(self) -> tuple[str, ...]:
        raise NotImplementedError

    @property
    def stdin(self) -> str | None:
        raise NotImplementedError

    @property
    def cwd(self) -> str:
        raise NotImplementedError

    @property
    def env(self) -> dict[str, str] | None:
        raise NotImplementedError


@dataclass(frozen=True)
class ClaudeCodeRunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    cancelled: bool


def is_context_dependent_implementation_request(request: str) -> bool:
    normalized = " ".join(request.strip().lower().split())
    return normalized in {
        "implement",
        "please implement",
        "code",
        "make the change",
        "make those changes",
    }


def build_claude_code_implementation_prompt(
    request: str,
    *,
    recent_messages: list[tuple[str, str]],
) -> str:
    context = ""
    if recent_messages:
        context = "\n".join(f"{role}: {text}" for role, text in recent_messages)
    context_block = (
        f"--- Recent OpenSRE terminal assistant context ---\n{context}\n\n" if context else ""
    )
    return (
        "You are Claude Code working in the current OpenSRE repository.\n\n"
        f"{context_block}"
        f"--- User implementation request ---\n{request.strip()}\n\n"
        "--- Rules ---\n"
        "- Implement the requested change in this repository.\n"
        "- Follow AGENTS.md, existing project conventions, and local code style.\n"
        "- Do not create a git commit or push changes.\n"
        "- Do not run destructive git commands such as reset --hard or checkout --.\n"
        "- Preserve unrelated user changes in the working tree.\n"
        "- Run focused tests or lint checks when practical.\n"
        "- Finish with a concise summary of changed files and verification performed.\n"
    )


def implementation_argv(argv: tuple[str, ...]) -> list[str]:
    exec_argv = list(argv)
    if not exec_argv:
        raise ValueError("Claude Code invocation is empty.")
    executable = Path(exec_argv[0])
    if not executable.is_absolute():
        raise ValueError("Claude Code executable path must be absolute.")
    permission_mode = os.environ.get(
        _IMPLEMENT_PERMISSION_MODE_ENV,
        _DEFAULT_IMPLEMENT_PERMISSION_MODE,
    ).strip()
    if permission_mode and permission_mode.lower() not in {"default", "none", "off"}:
        exec_argv.extend(["--permission-mode", permission_mode])
    return exec_argv


def spawn_claude_code(invocation: ClaudeInvocation) -> subprocess.Popen[str]:
    argv = implementation_argv(invocation.argv)
    cwd = str(Path(invocation.cwd).resolve())
    env = build_cli_subprocess_env(invocation.env)
    popen = cast("type[subprocess.Popen[str]]", subprocess.__dict__["Popen"])
    return popen(
        argv,
        stdin=subprocess.PIPE if invocation.stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        start_new_session=True,
    )


def run_claude_code_to_completion(
    proc: subprocess.Popen[str],
    invocation: ClaudeInvocation,
    *,
    cancel_event: threading.Event,
) -> ClaudeCodeRunResult:
    timed_out = False
    try:
        stdout, stderr = proc.communicate(
            input=invocation.stdin,
            timeout=CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_child_process(proc)
        stdout, stderr = proc.communicate()

    out = (stdout or "")[:MAX_COMMAND_OUTPUT_CHARS]
    err = (stderr or "")[:MAX_COMMAND_OUTPUT_CHARS]
    code = proc.returncode
    cancelled = cancel_event.is_set() and code != 0
    return ClaudeCodeRunResult(
        stdout=out,
        stderr=err,
        exit_code=code,
        timed_out=timed_out,
        cancelled=cancelled,
    )


def format_claude_failure_diag(stdout: str, stderr: str) -> str:
    return (stderr or stdout).strip()[:TASK_DIAG_CHARS]


def run_claude_code_implementation(request: str, presenter: SubprocessPresenter) -> None:
    session = presenter.session
    policy = allow_tool("code_agent")
    if not presenter.execution_allowed(
        policy,
        action_summary=f"Claude Code implementation: {request}",
    ):
        session.record("implementation", request, ok=False)
        return

    if is_context_dependent_implementation_request(request) and not session.agent.messages:
        presenter.print(
            "[error]implementation request is too vague:[/] "
            "describe what Claude Code should change."
        )
        session.record("implementation", request, ok=False)
        return

    adapter = ClaudeCodeAdapter()
    probe = adapter.detect()
    if not probe.installed or not probe.bin_path:
        presenter.print_error(f"Claude Code CLI not available: {probe.detail}")
        session.record("implementation", request, ok=False)
        return
    if probe.logged_in is False:
        presenter.print_error(f"Claude Code is not authenticated: {probe.detail}")
        session.record("implementation", request, ok=False)
        return

    recent = session.agent.messages[-6:]
    prompt = build_claude_code_implementation_prompt(request, recent_messages=recent)
    try:
        invocation = adapter.build(
            prompt=prompt,
            model=os.environ.get("CLAUDE_CODE_MODEL"),
            workspace=session_working_directory(session),
        )
    except Exception as exc:
        presenter.report_exception(exc, context="surfaces.interactive_shell.claude_code.build")
        presenter.print_error(f"Claude Code failed to prepare: {exc}")
        session.record("implementation", request, ok=False)
        return

    display_command = "claude -p"
    presenter.print_bold_command(display_command)
    task = session.task_registry.create(TaskKind.CODE_AGENT, command=display_command)
    task.mark_running()
    history_gen_when_started = session.terminal.history_generation

    try:
        proc = spawn_claude_code(invocation)
    except Exception as exc:
        task.mark_failed(str(exc))
        presenter.report_exception(exc, context="surfaces.interactive_shell.claude_code.start")
        presenter.print_error(f"Claude Code failed to start: {exc}")
        session.record("implementation", request, ok=False)
        return

    task.attach_process(proc)
    session.record("implementation", request, ok=True)

    def _watch() -> None:
        try:
            result = run_claude_code_to_completion(
                proc,
                invocation,
                cancel_event=task.cancel_requested,
            )
            if result.timed_out:
                task.mark_failed(f"timed out after {CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS}s")
                presenter.print(
                    f"[error]Claude Code timed out after "
                    f"{CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS} seconds[/]"
                )
                return

            if result.cancelled:
                task.mark_cancelled()
                if session.terminal.history_generation == history_gen_when_started:
                    session.mark_latest(ok=False, kind="implementation")
                presenter.print(f"[{_WARNING_STYLE}]Claude Code task cancelled.[/]")
                return

            if result.exit_code == 0:
                task.mark_completed(result="ok")
                presenter.print_highlight(f"Claude Code completed task {task.task_id}")
                presenter.print_command_output(result.stdout)
                if result.stderr:
                    presenter.print_command_output(result.stderr, style=_DIM_STYLE)
                return

            diag = format_claude_failure_diag(result.stdout, result.stderr)
            error_msg = f"exit code {result.exit_code}" + (f": {diag}" if diag else "")
            task.mark_failed(error_msg)
            if session.terminal.history_generation == history_gen_when_started:
                session.mark_latest(ok=False, kind="implementation")
            presenter.print(f"[error]Claude Code failed (exit {result.exit_code}):[/]")
            presenter.print_command_output(result.stdout)
            presenter.print_command_output(result.stderr, style=_ERROR_STYLE)
        except Exception as exc:  # noqa: BLE001
            task.mark_failed(str(exc))
            presenter.report_exception(exc, context="surfaces.interactive_shell.claude_code.watch")
            if session.terminal.history_generation == history_gen_when_started:
                session.mark_latest(ok=False, kind="implementation")
            presenter.print_error(f"Claude Code watcher failed: {exc}")

    threading.Thread(target=_watch, daemon=True, name=f"claude-code-{task.task_id}").start()
    presenter.print(
        f"[dim]Claude Code started — task[/] [bold]{task.task_id}[/bold]. "
        "[highlight]/tasks[/] [dim]to monitor,[/] "
        f"[highlight]/cancel {task.task_id}[/] [dim]to stop.[/]"
    )


__all__ = ["run_claude_code_implementation"]
