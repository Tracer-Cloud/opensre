"""Direct unit tests for ``subprocess_runner`` (complement to ``test_agent_actions``)."""

from __future__ import annotations

import errno
import io
import subprocess
import tempfile
import threading
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from infrastructure.scheduling.task_types import TaskKind, TaskStatus
from infrastructure.terminal.theme import GLYPH_ERROR, GLYPH_SUCCESS
from integrations.llm_cli.base import CLIInvocation, CLIProbe
from surfaces.interactive_shell.runtime.subprocess_runner import (
    _MIN_SUBPROCESS_TERMINAL_WIDTH,
    _TASK_OUTPUT_PREFIX_WIDTH,
    _pump_task_pty,
    _pump_task_stream,
    read_diag,
    read_task_output,
    run_opensre_cli_command,
    start_background_cli_task,
    terminate_child_process,
)
from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import make_repl_presenter
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.cli import is_interactive_wizard
from tools.interactive_shell.implementation.claude_code_executor import (
    run_claude_code_implementation,
)
from tools.interactive_shell.shell.execution import (
    ShellExecutionResult,
)
from tools.interactive_shell.shell.runner import (
    run_cd_command,
    run_pwd_command,
    run_shell_command,
)

_BACKGROUND_TASK_POPEN = "surfaces.interactive_shell.runtime.subprocess_runner.subprocess.Popen"
_CLI_POPEN = "tools.interactive_shell.cli.subprocess.Popen"
_CLI_RUN = "tools.interactive_shell.cli.subprocess.run"


def _presenter(
    session: Session,
    console: Console,
    *,
    confirm_fn: object = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
):
    return make_repl_presenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


class _ImmediateThread:
    def __init__(
        self,
        group: object = None,
        target: object = None,
        name: object = None,
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        *,
        daemon: object = None,
    ) -> None:
        del group, name, daemon
        if not callable(target):
            raise TypeError("target required")
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        self._target(*self._args, **self._kwargs)

    def join(self, timeout: float | None = None) -> None:
        self.join_timeout = timeout


def test_terminate_child_process_noop_when_exited() -> None:
    proc = MagicMock()
    proc.poll.return_value = 0
    terminate_child_process(proc)
    proc.terminate.assert_not_called()


def test_read_diag_respects_byte_cap() -> None:
    with tempfile.SpooledTemporaryFile() as buf:  # type: ignore[type-arg]
        buf.write(b"z" * 5_000)
        text = read_diag(buf)
    assert len(text) == 2_000


def test_read_task_output_handles_none_buffer() -> None:
    assert read_task_output(None, limit=100) == ""


def test_read_task_output_strips_ansi_and_caps() -> None:
    with tempfile.SpooledTemporaryFile() as buf:  # type: ignore[type-arg]
        buf.write(b"\x1b[31merror\x1b[0m line\n")
        text = read_task_output(buf, limit=1_000)
    assert text == "error line"


def test_read_task_output_returns_empty_for_closed_buffer() -> None:
    with tempfile.SpooledTemporaryFile() as buf:  # type: ignore[type-arg]
        buf.write(b"data")
    # Buffer is closed once the ``with`` block exits.
    assert read_task_output(buf, limit=100) == ""


def test_run_pwd_command_prints_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_cwd(_: type[Path]) -> PurePosixPath:
        return PurePosixPath("/shown/pwd")

    monkeypatch.setattr(Path, "cwd", classmethod(_fake_cwd))

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_pwd_command("pwd", _presenter(session, console))
    assert "/shown/pwd" in buf.getvalue()
    assert session.history[-1]["type"] == "shell"


def test_run_pwd_command_rejects_multiple_tokens() -> None:
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_pwd_command("pwd extra", _presenter(session, console))
    assert "too many arguments" in buf.getvalue().lower()
    assert session.history[-1]["ok"] is False


def test_run_cd_command_chdirs_to_target(monkeypatch: pytest.MonkeyPatch) -> None:
    directories: list[Path] = []

    def _chdir(target: Path) -> None:
        directories.append(target)

    monkeypatch.setattr(
        "tools.interactive_shell.shell.runner.os.chdir",
        _chdir,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_cd_command("cd /tmp/example", _presenter(session, console))
    assert directories == [Path("/tmp/example")]
    assert session.history[-1]["type"] == "shell"


def test_run_shell_command_quiet_cd_hides_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.interactive_shell.shell.runner.os.chdir",
        lambda _target: None,
    )
    monkeypatch.setattr(
        "tools.interactive_shell.shell.runner.Path.cwd",
        classmethod(lambda _cls: Path("/tmp/example")),
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    result = run_shell_command("cd /tmp/example", _presenter(session, console), quiet=True)

    assert "$" not in buf.getvalue()
    assert "/tmp/example" not in buf.getvalue()
    assert result["ok"] is True
    assert result["response_text"] == "/tmp/example"


def test_run_cd_command_reports_chdir_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_errors: list[BaseException] = []

    def _chdir(_target: Path) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(
        "tools.interactive_shell.shell.runner.os.chdir",
        _chdir,
    )
    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_cd_command("cd /root/blocked", _presenter(session, console))

    assert "cd failed" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], OSError)
    assert session.history[-1] == {
        "type": "shell",
        "text": "cd /root/blocked",
        "ok": False,
        "response_text": "cd failed: permission denied",
    }


def test_run_shell_command_records_when_input_is_empty() -> None:
    """Alpha mode allows every command; only empty input is rejected.

    A bare ``!`` has nothing to run, so the REPL prints a ``blocked`` notice,
    never echoes a command, and records the attempt as ``ok=False``. (Restricted
    commands like ``sudo`` are no longer blocked — there is no deny floor.)
    """
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command(
        "!",
        _presenter(session, console, confirm_fn=lambda _p: "n", is_tty=True),
    )

    out = buf.getvalue()
    assert "blocked" in out.lower()
    # Nothing should be echoed/executed for empty input.
    assert "$ " not in out
    assert session.history[-1] == {"type": "shell", "text": "!", "ok": False}


def test_run_claude_code_implementation_starts_tracked_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[tuple[list[str], dict[str, object]]] = []
    stdin_seen: list[str | None] = []

    class _FakeAdapter:
        def detect(self) -> CLIProbe:
            return CLIProbe(
                installed=True,
                version="1.2.3",
                logged_in=True,
                bin_path="/usr/local/bin/claude",
                detail="ok",
            )

        def build(
            self,
            *,
            prompt: str,
            model: str | None,
            workspace: str,
            reasoning_effort: str | None = None,
        ) -> CLIInvocation:
            assert model is None
            assert workspace
            assert reasoning_effort is None
            assert "Recent OpenSRE terminal assistant context" in prompt
            assert "Process auto-discovery" in prompt
            assert "Do not create a git commit" in prompt
            return CLIInvocation(
                argv=("/usr/local/bin/claude", "-p", "--output-format", "text"),
                stdin=prompt,
                cwd=workspace,
                env={"CLAUDE_TEST": "1"},
                timeout_sec=120.0,
            )

    class _FakeProcess:
        returncode = 0

        def communicate(
            self,
            input: str | None = None,
            timeout: int | None = None,
        ) -> tuple[str, str]:
            assert timeout is not None
            stdin_seen.append(input)
            return "changed interactive_shell\n", ""

        def poll(self) -> int:
            return 0

    def _fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        popen_calls.append((command, kwargs))
        return _FakeProcess()

    monkeypatch.delenv("CLAUDE_CODE_IMPLEMENT_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.setattr(
        "tools.interactive_shell.implementation.claude_code_executor.ClaudeCodeAdapter",
        _FakeAdapter,
    )
    monkeypatch.setattr(
        "tools.interactive_shell.implementation.claude_code_executor.subprocess.Popen",
        _fake_popen,
    )
    monkeypatch.setattr(
        "tools.interactive_shell.implementation.claude_code_executor.threading.Thread",
        _ImmediateThread,
    )

    session = Session()
    session.agent.messages.append(
        ("assistant", "Process auto-discovery should scan local agent processes.")
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_claude_code_implementation(
        "implement",
        _presenter(session, console, confirm_fn=lambda _prompt: "y", is_tty=True),
    )

    assert len(popen_calls) == 1
    command, kwargs = popen_calls[0]
    assert command == [
        "/usr/local/bin/claude",
        "-p",
        "--output-format",
        "text",
        "--permission-mode",
        "acceptEdits",
    ]
    assert kwargs["cwd"]
    assert stdin_seen and "Process auto-discovery" in stdin_seen[0]
    assert session.history[-1] == {"type": "implementation", "text": "implement", "ok": True}
    task = session.task_registry.list_recent(1)[0]
    assert task.kind == TaskKind.CODE_AGENT
    assert task.status == TaskStatus.COMPLETED
    out = buf.getvalue()
    assert "Claude Code started" in out
    assert "Claude Code completed" in out


def test_run_claude_code_implementation_rejects_vague_request_without_context() -> None:
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_claude_code_implementation(
        "implement",
        _presenter(session, console, confirm_fn=lambda _prompt: "y", is_tty=True),
    )

    assert "too vague" in buf.getvalue()
    assert session.history[-1] == {"type": "implementation", "text": "implement", "ok": False}
    assert session.task_registry.list_recent(1) == []


def test_run_shell_command_outputless_success_omits_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_execute(**_kwargs: object) -> ShellExecutionResult:
        return ShellExecutionResult(
            command="true",
            argv=["true"],
            stdout="",
            stderr="",
            exit_code=0,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
        )

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _fake_execute,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command("true", _presenter(session, console))
    output = buf.getvalue()
    assert "$ true" in output
    assert GLYPH_SUCCESS not in output
    assert session.history[-1] == {"type": "shell", "text": "true", "ok": True}


def test_run_shell_command_quiet_hides_command_and_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_execute(**_kwargs: object) -> ShellExecutionResult:
        return ShellExecutionResult(
            command="echo hi",
            argv=["echo", "hi"],
            stdout="hi\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
        )

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _fake_execute,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    result = run_shell_command("echo hi", _presenter(session, console), quiet=True)
    out = buf.getvalue()
    assert "$" not in out
    assert "hi" not in out
    assert GLYPH_SUCCESS not in out
    assert result["ok"] is True
    assert result["stdout"] == "hi"
    assert result["response_text"] == "hi"
    assert session.history[-1]["ok"] is True


def test_run_shell_command_quiet_outputless_success_prints_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quiet ``touch`` must not print a live success glyph.

    Quiet hides ``$`` and stdout. Outputless success has neither; a live
    marker would still leak intermediate probes before a composed closing.
    Loud mode prints the glyph. Quiet leaves the terminal blank here — the
    action closer (kept for quiet ``shell_run``) is the turn's display.
    """

    def _fake_execute(**_kwargs: object) -> ShellExecutionResult:
        return ShellExecutionResult(
            command="touch file",
            argv=["touch", "file"],
            stdout="",
            stderr="",
            exit_code=0,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
        )

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _fake_execute,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    result = run_shell_command("touch file", _presenter(session, console), quiet=True)

    out = buf.getvalue()
    assert out == ""
    assert GLYPH_SUCCESS not in out
    assert result["ok"] is True
    assert "response_text" not in result
    assert session.history[-1] == {
        "type": "shell",
        "text": "touch file",
        "ok": True,
    }


def test_run_shell_command_success_records_stdout_without_stderr_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_execute(**_kwargs: object) -> ShellExecutionResult:
        return ShellExecutionResult(
            command="curl wttr.in/Hawaii?format=3",
            argv=["curl", "wttr.in/Hawaii?format=3"],
            stdout="Hawaii: +25C\n",
            stderr="curl progress\n",
            exit_code=0,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
        )

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _fake_execute,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    result = run_shell_command("curl wttr.in/Hawaii?format=3", _presenter(session, console))

    assert session.history[-1] == {
        "type": "shell",
        "text": "curl wttr.in/Hawaii?format=3",
        "ok": True,
        "response_text": "Hawaii: +25C",
    }
    assert result["ok"] is True
    assert result["stdout"] == "Hawaii: +25C"
    assert result["stderr"] == ""
    assert result["response_text"] == "Hawaii: +25C"


def test_run_shell_command_failure_prints_exit_line(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_execute(**_kwargs: object) -> ShellExecutionResult:
        return ShellExecutionResult(
            command="false",
            argv=["false"],
            stdout="",
            stderr="",
            exit_code=7,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
        )

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _fake_execute,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command("false", _presenter(session, console))
    out = buf.getvalue()
    assert GLYPH_ERROR in out
    assert "exit 7" in out
    assert session.history[-1] == {
        "type": "shell",
        "text": "false",
        "ok": False,
        "response_text": f"{GLYPH_ERROR} exit 7",
    }


def test_run_shell_command_reports_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    cancel = threading.Event()

    def _fake_execute(**kwargs: object) -> ShellExecutionResult:
        seen.update(kwargs)
        return ShellExecutionResult(
            command="sleep 30",
            argv=["sleep", "30"],
            stdout="",
            stderr="",
            exit_code=-15,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
            cancelled=True,
        )

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _fake_execute,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    result = run_shell_command(
        "sleep 30",
        _presenter(session, console),
        cancel_event=cancel,
    )
    assert seen["cancel_event"] is cancel
    assert result["ok"] is False
    assert result["cancelled"] is True
    assert result["response_text"] == "command cancelled"
    assert "command cancelled" in buf.getvalue()
    assert session.history[-1]["ok"] is False


def test_run_shell_command_reports_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_errors: list[BaseException] = []

    def _raise(**_kwargs: object) -> ShellExecutionResult:
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.execute_shell_command",
        _raise,
    )
    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    run_shell_command("true", _presenter(session, console))

    assert "command failed to start" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)
    assert session.history[-1] == {
        "type": "shell",
        "text": "true",
        "ok": False,
        "response_text": "command failed to start: spawn failed",
    }


def test_run_opensre_agents_scan_prints_clean_foreground_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[-2:] == ["fleet", "scan"]
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="agent scan\n777 claude-code-777 claude code\nNext: register\n",
            stderr="",
        )

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.subprocess.run",
        _fake_run,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert run_opensre_cli_command("fleet scan", session, console) is True

    out = buf.getvalue()
    assert "$ opensre fleet scan" in out
    assert "agent scan" in out
    assert "777 claude-code-777 claude code" in out
    assert "started." not in out
    assert "stdout │" not in out
    assert session.history[-1] == {"type": "cli_command", "text": "opensre fleet scan", "ok": True}


def test_run_opensre_agents_scan_register_explains_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="registered 1 agent(s)\n",
            stderr="",
        )

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.subprocess.run",
        _fake_run,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "fleet scan --register",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "register discovered local AI-agent processes" in out
    assert "registered 1 agent(s)" in out
    assert "stdout │" not in out
    assert session.history[-1] == {
        "type": "cli_command",
        "text": "opensre fleet scan --register",
        "ok": True,
    }


def test_run_opensre_agents_watch_runs_in_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_kwargs: list[dict[str, object]] = []

    class _FakeProcess:
        stdout = iter(["watching pid 1234; press Ctrl+C to stop\n", "pid 1234 exited\n"])

        def wait(self) -> int:
            return 0

    def _fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        assert command[-3:] == ["fleet", "watch", "1234"]
        popen_kwargs.append(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(
        _CLI_POPEN,
        _fake_popen,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "fleet watch 1234",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "$ opensre fleet watch 1234" in out
    assert "watching pid 1234" in out
    assert "pid 1234 exited" in out
    assert "started" not in out
    assert "timeout" not in popen_kwargs[0]
    assert popen_kwargs[0]["stderr"] is subprocess.STDOUT
    assert session.task_registry.list_recent() == []
    assert session.history[-1] == {
        "type": "cli_command",
        "text": "opensre fleet watch 1234",
        "ok": True,
    }


def test_start_background_cli_task_uses_pty_for_live_terminal_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_kwargs: list[dict[str, object]] = []
    closed_fds: list[int] = []
    chunks = [b"live progress\r\n"]

    class _TtyBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    class _FakeProcess:
        returncode = 0
        stdout = None
        stderr = None

        def poll(self) -> int:
            return 0

    def _fake_popen(_command: list[str], **kwargs: object) -> _FakeProcess:
        popen_kwargs.append(kwargs)
        return _FakeProcess()

    def _fake_read(fd: int, _size: int) -> bytes:
        assert fd == 10
        if chunks:
            return chunks.pop(0)
        raise OSError(errno.EIO, "pty closed")

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.os.openpty",
        lambda: (10, 11),
        raising=False,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.os.read",
        _fake_read,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.os.close",
        lambda fd: closed_fds.append(fd),
    )
    monkeypatch.setattr(
        _BACKGROUND_TASK_POPEN,
        _fake_popen,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = Session()
    buf = _TtyBuffer()
    console = Console(file=buf, force_terminal=True)

    task = start_background_cli_task(
        display_command="opensre fleet scan",
        argv_list=["python", "-m", "cli", "fleet", "scan"],
        session=session,
        console=console,
        kind=TaskKind.CLI_COMMAND,
        use_pty=True,
    )

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert popen_kwargs[0]["stdout"] == 11
    assert popen_kwargs[0]["stderr"] == 11
    assert "text" not in popen_kwargs[0]
    assert "live progress" in buf.getvalue()
    assert 10 in closed_fds
    assert 11 in closed_fds


def test_start_background_cli_task_falls_back_to_pipes_when_pty_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_kwargs: list[dict[str, object]] = []

    class _TtyBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    class _FakeProcess:
        returncode = 0
        stdout = io.StringIO("pipe progress\n")
        stderr = io.StringIO("")

        def poll(self) -> int:
            return 0

    def _fake_popen(_command: list[str], **kwargs: object) -> _FakeProcess:
        popen_kwargs.append(kwargs)
        return _FakeProcess()

    monkeypatch.delattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.os.openpty",
        raising=False,
    )
    monkeypatch.setattr(
        _BACKGROUND_TASK_POPEN,
        _fake_popen,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = Session()
    buf = _TtyBuffer()
    console = Console(file=buf, force_terminal=True)

    task = start_background_cli_task(
        display_command="opensre fleet scan",
        argv_list=["python", "-m", "cli", "fleet", "scan"],
        session=session,
        console=console,
        kind=TaskKind.CLI_COMMAND,
        use_pty=True,
    )

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert popen_kwargs[0]["stdout"] is subprocess.PIPE
    assert popen_kwargs[0]["stderr"] is subprocess.PIPE
    assert popen_kwargs[0]["text"] is True
    assert "pipe progress" in buf.getvalue()


def test_start_background_cli_task_logs_failure_outcome_to_posthog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A background CLI task that fails asynchronously must still send its real
    stderr/exit outcome to the prompt-log/PostHog sink. This is the regression
    for background CLI errors arriving after the turn recorder flushed.
    """
    captured: list[dict[str, object]] = []

    monkeypatch.setenv("OPENSRE_PROMPT_LOG_REDACT", "0")
    monkeypatch.setenv("OPENSRE_PROMPT_LOG_LOCAL_DISABLED", "1")
    monkeypatch.setattr(
        "surfaces.interactive_shell.telemetry.recorder.capture_ai_generation",
        lambda properties: captured.append(properties),
    )

    class _FakeProcess:
        returncode = 1
        stdout = io.StringIO("")
        stderr = io.StringIO("No remote named 'myportfolio' is configured.\n")

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(
        _BACKGROUND_TASK_POPEN,
        lambda _command, **_kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre fleet watch 1234",
        argv_list=["python", "-m", "cli", "fleet", "watch", "1234"],
        session=session,
        console=console,
        kind=TaskKind.CLI_COMMAND,
    )

    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert len(captured) == 1
    props = captured[0]
    assert props["cli_turn_kind"] == "background_task"
    assert props["$ai_trace_id"] == task.task_id
    ai_input = props["$ai_input"]
    assert isinstance(ai_input, list)
    assert ai_input[0]["content"] == "opensre fleet watch 1234"
    choices = props["$ai_output_choices"]
    assert isinstance(choices, list)
    content = choices[0]["content"]
    assert "command failed (exit 1)" in content
    assert "No remote named 'myportfolio' is configured." in content


def test_start_background_cli_task_logs_success_outcome_to_posthog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful background task logs its stdout outcome, not an empty reply."""
    captured: list[dict[str, object]] = []

    monkeypatch.setenv("OPENSRE_PROMPT_LOG_REDACT", "0")
    monkeypatch.setenv("OPENSRE_PROMPT_LOG_LOCAL_DISABLED", "1")
    monkeypatch.setattr(
        "surfaces.interactive_shell.telemetry.recorder.capture_ai_generation",
        lambda properties: captured.append(properties),
    )

    class _FakeProcess:
        returncode = 0
        stdout = io.StringIO("scan complete: 2 agents registered\n")
        stderr = io.StringIO("")

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(
        _BACKGROUND_TASK_POPEN,
        lambda _command, **_kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre fleet scan --register",
        argv_list=["python", "-m", "cli", "fleet", "scan", "--register"],
        session=session,
        console=console,
        kind=TaskKind.CLI_COMMAND,
    )

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert len(captured) == 1
    content = captured[0]["$ai_output_choices"][0]["content"]
    assert "command completed (exit 0)" in content
    assert "scan complete: 2 agents registered" in content


def test_task_output_stream_reports_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []

    class _BrokenStream:
        def __iter__(self) -> _BrokenStream:
            return self

        def __next__(self) -> str:
            raise RuntimeError("stream broke")

    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    session = Session()
    task = session.task_registry.create(TaskKind.CLI_COMMAND, command="demo")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    _pump_task_stream(
        task=task,
        stream_name="stdout",
        stream=_BrokenStream(),  # type: ignore[arg-type]
        console=console,
    )

    assert "stream ended unexpectedly" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)


def test_task_pty_stream_reports_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []
    closed_fds: list[int] = []

    def _raise_read(_fd: int, _size: int) -> bytes:
        raise RuntimeError("pty broke")

    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.os.read",
        _raise_read,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.os.close",
        lambda fd: closed_fds.append(fd),
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    with tempfile.SpooledTemporaryFile() as capture:  # type: ignore[type-arg]
        _pump_task_pty(master_fd=123, console=console, capture=capture)

    assert "terminal stream ended unexpectedly" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)
    assert closed_fds == [123]


def test_start_background_cli_task_reports_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []

    def _fake_popen(_command: list[str], **_kwargs: object) -> object:
        raise RuntimeError("spawn broke")

    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    monkeypatch.setattr(
        _BACKGROUND_TASK_POPEN,
        _fake_popen,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre fleet scan",
        argv_list=["python", "-m", "cli", "fleet", "scan"],
        session=session,
        console=console,
        kind=TaskKind.CLI_COMMAND,
    )

    assert task is None
    assert "failed to start" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)
    assert session.task_registry.list_recent(1)[0].status == TaskStatus.FAILED


def test_start_background_cli_task_reports_watcher_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []

    class _FakeProcess:
        stdout = None
        stderr = None
        returncode = 1

        def poll(self) -> int:
            return 1

    def _fake_popen(_command: list[str], **_kwargs: object) -> _FakeProcess:
        return _FakeProcess()

    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    monkeypatch.setattr(
        _BACKGROUND_TASK_POPEN,
        _fake_popen,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.read_diag",
        lambda _buf: (_ for _ in ()).throw(RuntimeError("diag broke")),
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command="opensre fleet scan",
        argv_list=["python", "-m", "cli", "fleet", "scan"],
        session=session,
        console=console,
        kind=TaskKind.CLI_COMMAND,
    )

    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert "error:" in buf.getvalue()
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess terminal width forwarding
# ─────────────────────────────────────────────────────────────────────────────
#
# Regression: subprocess Rich output (panels and tables) used to render at the
# default 80-column width because the subprocess's stdout is a pipe.
# ``_print_task_output_line`` then prepended an 18-char ``<task_id>
# <stream> │ `` prefix, producing 98-char lines that wrapped mid-row in the
# user's narrower terminal — the visible symptom was broken table headers and
# panel borders. We forward ``user_width - prefix - 1`` via ``COLUMNS`` so the
# subprocess renders narrow enough that the relayed line fits intact.


class _CapturedPopen:
    returncode = 0
    stdout = io.StringIO("")
    stderr = io.StringIO("")

    def poll(self) -> int:
        return 0


def _capture_popen_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    popen_target: str = _BACKGROUND_TASK_POPEN,
) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def _fake_popen(_command: list[str], **kwargs: object) -> _CapturedPopen:
        captured.append(kwargs)
        return _CapturedPopen()

    monkeypatch.setattr(
        popen_target,
        _fake_popen,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )
    return captured


def _start_one_task(console: Console) -> None:
    start_background_cli_task(
        display_command="opensre fleet scan",
        argv_list=["opensre", "fleet", "scan"],
        session=Session(),
        console=console,
        kind=TaskKind.CLI_COMMAND,
    )


def test_background_task_forwards_columns_minus_prefix_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subprocess COLUMNS = user_width − task-output prefix − 1.

    A 100-col user terminal must hand the subprocess ``100 − 18 − 1 = 81``
    columns so its Rich rendering fits within the user's terminal once the
    18-char ``<task_id> stdout │ `` prefix is prepended by the line pump.
    """
    captured = _capture_popen_kwargs(monkeypatch)
    console = Console(file=io.StringIO(), force_terminal=False, width=100)

    _start_one_task(console)

    assert captured, "Popen must have been called"
    env = captured[0].get("env")
    assert isinstance(env, dict)
    assert env.get("COLUMNS") == str(100 - _TASK_OUTPUT_PREFIX_WIDTH - 1)


def test_background_task_floors_subprocess_columns_for_tiny_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COLUMNS must never go below ``_MIN_SUBPROCESS_TERMINAL_WIDTH``.

    On absurdly narrow terminals (CI jobs, restricted SSH PTYs) the naive
    ``width − prefix − 1`` calculation could fall below the point where Rich
    panels render at all. We floor at 60 so borders remain drawable; visible
    wrapping is better than a crushed rendering.
    """
    captured = _capture_popen_kwargs(monkeypatch)
    console = Console(file=io.StringIO(), force_terminal=False, width=40)

    _start_one_task(console)

    env = captured[0].get("env")
    assert isinstance(env, dict)
    assert env.get("COLUMNS") == str(_MIN_SUBPROCESS_TERMINAL_WIDTH)


def test_background_task_preserves_existing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The forwarded env must inherit ``os.environ`` so the subprocess sees PATH etc.

    We only inject COLUMNS/LINES; everything else (PATH, HOME, virtualenv,
    auth tokens) must reach the subprocess unchanged.
    """
    monkeypatch.setenv("OPENSRE_TEST_MARKER", "preserved-value")
    captured = _capture_popen_kwargs(monkeypatch)
    console = Console(file=io.StringIO(), force_terminal=False, width=120)

    _start_one_task(console)

    env = captured[0].get("env")
    assert isinstance(env, dict)
    assert env.get("OPENSRE_TEST_MARKER") == "preserved-value"


@pytest.mark.parametrize(
    "tokens,expected",
    [
        # Single-token interactive wizards.
        (["onboard"], True),
        (["ONBOARD"], True),  # case-insensitive
        (["onboard", "local_llm"], True),  # extra args still classified
        # Two-token interactive wizard.
        (["integrations", "setup"], True),
        (["INTEGRATIONS", "SETUP"], True),
        (["integrations", "setup", "datadog"], True),
        # Two-token NON-wizard under integrations — must NOT match.
        (["integrations", "list"], False),
        (["integrations", "verify"], False),
        # Other subcommands — must NOT match.
        (["health"], False),
        (["version"], False),
        (["fleet", "list"], False),
        # Edge: empty.
        ([], False),
    ],
)
def test_is_interactive_wizard_classifies_command_paths(tokens: list[str], expected: bool) -> None:
    """The wizard classifier is the data-driven contract for the LLM-classified
    path (``cli_exec`` tool). When the LLM tries to invoke a wizard via
    ``cli_exec``, we redirect the user to the equivalent slash command instead
    because exclusive stdin is only guaranteed for the slash-command path.
    Adding a new interactive command later should be a one-line set entry —
    this test pins the current set + the case-insensitive lookup behavior.
    """
    assert is_interactive_wizard(tokens) is expected


def test_run_opensre_cli_command_refuses_onboard_with_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM-classified path (``cli_exec`` tool) for ``opensre onboard``
    must not spawn a subprocess — exclusive stdin is not guaranteed when the
    LLM planner is involved. It must instead print a message directing the
    user to the ``/onboard`` slash command, which does have exclusive stdin.
    """
    popen_calls: list[list[str]] = []
    run_calls: list[list[str]] = []

    def _fake_popen(command: list[str], **_kwargs: object) -> None:
        popen_calls.append(command)
        raise AssertionError("subprocess.Popen must not be called for interactive subcommand")

    def _fake_run(command: list[str], **_kwargs: object) -> None:
        run_calls.append(command)
        raise AssertionError("subprocess.run must not be called for interactive subcommand")

    monkeypatch.setattr(
        _CLI_POPEN,
        _fake_popen,
    )
    monkeypatch.setattr(
        _CLI_RUN,
        _fake_run,
    )

    session = Session()
    buf = io.StringIO()
    # Width >80 so the multi-line warning doesn't wrap mid-substring on
    # the assertions below.
    console = Console(file=buf, force_terminal=False, width=200)

    assert (
        run_opensre_cli_command(
            "onboard",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "needs a full terminal" in out
    assert "opensre onboard" in out
    # Directs user to the slash command (not "exit the shell").
    assert "/onboard" in out
    assert popen_calls == []
    assert run_calls == []
    assert session.history[-1]["type"] == "cli_command"
    assert session.history[-1]["text"] == "opensre onboard"
    assert session.history[-1]["ok"] is False
    assert "full terminal" in str(session.history[-1].get("response_text", ""))
    assert "/onboard" in str(session.history[-1].get("response_text", ""))


def test_run_opensre_cli_command_refuses_integrations_setup_with_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``opensre integrations setup`` via the LLM ``cli_exec`` path must redirect
    to ``/integrations setup`` (which has exclusive stdin). Same pattern as ``onboard``.
    """
    popen_calls: list[list[str]] = []
    run_calls: list[list[str]] = []

    monkeypatch.setattr(
        _CLI_POPEN,
        lambda cmd, **_kw: popen_calls.append(cmd),
    )
    monkeypatch.setattr(
        _CLI_RUN,
        lambda cmd, **_kw: run_calls.append(cmd),
    )

    session = Session()
    buf = io.StringIO()
    # Width >80 so the multi-line warning doesn't wrap mid-substring on
    # assertions below.
    console = Console(file=buf, force_terminal=False, width=200)

    assert (
        run_opensre_cli_command(
            "integrations setup",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "needs a full terminal" in out
    assert "opensre integrations setup" in out
    # Directs user to the slash command (not "exit the shell").
    assert "/integrations setup" in out
    assert popen_calls == []
    assert run_calls == []
    assert session.history[-1]["type"] == "cli_command"
    assert session.history[-1]["text"] == "opensre integrations setup"
    assert session.history[-1]["ok"] is False
    assert "full terminal" in str(session.history[-1].get("response_text", ""))


def test_run_opensre_cli_command_runs_integrations_list_in_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``integrations list`` is read-only: it runs foreground so the action turn
    observes the result, and is not caught by the interactive-wizard block (only
    ``integrations setup`` is the wizard). Backgrounding it left the plan parked
    on a task id while the turn ended.
    """
    start_calls: list[list[str]] = []

    def _fake_start_background_cli_task(*, argv_list: list[str], **_kw: object) -> None:
        start_calls.append(argv_list)

    def _fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="SERVICE  STATUS  ID\ngithub   active  github-c943a332\n",
            stderr="",
        )

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.start_background_cli_task",
        _fake_start_background_cli_task,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.subprocess.run",
        _fake_run,
    )

    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert (
        run_opensre_cli_command(
            "integrations list",
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        is True
    )

    out = buf.getvalue()
    assert "needs a full terminal" not in out  # wizard block did not fire
    assert "$ opensre integrations list" in out
    assert "github-c943a332" in out
    # Read-only integrations runs foreground, not as a background task, so the
    # turn observes pass/fail instead of ending on a task id.
    assert start_calls == []


def test_start_background_cli_task_echoes_command_markup_literally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``$ <command>`` header must render Rich markup literally.

    ``[/api/checkout]`` raised ``MarkupError``; ``[error]`` was swallowed.
    """
    monkeypatch.setenv("OPENSRE_PROMPT_LOG_LOCAL_DISABLED", "1")

    class _FakeProcess:
        returncode = 0
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(_BACKGROUND_TASK_POPEN, lambda _command, **_kwargs: _FakeProcess())
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.threading.Thread",
        _ImmediateThread,
    )

    display_command = 'opensre fleet scan --note "[error] 5xx spike on [/api/checkout]"'
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    task = start_background_cli_task(
        display_command=display_command,
        argv_list=["python", "-m", "cli", "fleet", "scan"],
        session=session,
        console=console,
    )

    assert task is not None
    assert f"$ {display_command}" in buf.getvalue().replace("\n", "")


def test_highlight_command_leaves_argument_keywords_plain() -> None:
    """``done`` after ``echo`` is an argument, not the bash loop keyword — a shell
    lexer mis-colours it. Command words are highlighted; ``done`` stays plain."""
    from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import (
        _highlight_command,
    )

    text = _highlight_command("sleep 8 && echo done")
    styled = {text.plain[s.start : s.end].strip() for s in text.spans}
    assert "sleep" in styled  # first command highlighted
    assert "echo" in styled  # command after the operator highlighted
    assert "done" not in styled  # argument left in the base style, not recoloured
