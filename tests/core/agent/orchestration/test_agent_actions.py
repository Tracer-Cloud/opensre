"""Tests for terminal action execution in the interactive terminal assistant."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path, PurePosixPath

import pytest
from rich.console import Console

import config.constants.platform as platform_module
import surfaces.interactive_shell.runtime.action_turn as action_turn
import surfaces.interactive_shell.runtime.llm_provider_adapter as llm_provider_adapter
import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
import surfaces.interactive_shell.runtime.subprocess_runner as subprocess_runner
import tests.shared.harness_turn_driver as harness_turn_driver
import tools.interactive_shell.shell.execution as shell_execution
from core.llm.types import AgentLLMResponse, ToolCall
from surfaces.interactive_shell.session import Session
from tests.core.agent._planned_action import (
    PlannedAction,
    default_target_surface,
)
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
)
from tools.interactive_shell.action_names import (
    TOOL_KIND_TO_NAME,
    ToolKind,
)
from tools.interactive_shell.subprocess import SubprocessWatchResult

_ACTION_LLM_FACTORY_PATCHES = (
    "core.agent_harness.turns.headless_build.default_llm_factory",
    "surfaces.interactive_shell.runtime.action_turn.default_llm_factory",
)


def _patch_action_llm_factory(monkeypatch: pytest.MonkeyPatch, value: object) -> None:
    """Patch the default LLM factory wherever the exercised call path resolves it.

    Tests in this file drive the action turn through both entry points --
    ``run_harness_turn`` (-> HeadlessAgent -> headless_build.default_llm_factory)
    and ``action_turn.run_action_tool_turn`` directly (its own module-level
    default_llm_factory binding) -- so both locations are patched; the unused
    one is simply never read.
    """
    for target in _ACTION_LLM_FACTORY_PATCHES:
        monkeypatch.setattr(target, value)


run_harness_turn = harness_turn_driver.run_harness_turn


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _action(
    kind: ToolKind,
    content: str,
    position: int = 0,
    *,
    args: dict[str, object] | None = None,
) -> PlannedAction:
    """Build a ``PlannedAction`` as the LLM planner would emit it."""
    return PlannedAction(
        kind=kind,
        content=content,
        position=position,
        source="llm",
        target_surface=default_target_surface(kind),
        args=dict(args) if args else {},
    )


def _tool_args_for_action(action: PlannedAction) -> dict[str, object]:
    if action.args:
        return dict(action.args)
    content = action.content.strip()
    if action.kind == "slash":
        parts = content.split()
        return {
            "command": parts[0] if parts else "",
            "args": parts[1:] if len(parts) > 1 else [],
        }
    if action.kind == "llm_provider":
        return {"target": content}
    if action.kind == "shell":
        return {"command": content}
    if action.kind == "task_cancel":
        return {"target": content}
    if action.kind == "cli_command":
        return {"payload": content}
    if action.kind == "implementation":
        return {"task": content}
    return {"content": content}


def _response_from_actions(actions: list[PlannedAction]) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[
            ToolCall(
                id=f"call_{index}",
                name=TOOL_KIND_TO_NAME[action.kind],
                input=_tool_args_for_action(action),
            )
            for index, action in enumerate(actions)
        ],
        raw_content=None,
    )


def _message_from_agent_prompt(messages: list[dict[str, object]]) -> str:
    """Extract what the user literally typed from the built user message.

    The literal envelope is one segment of the message, not the whole of it:
    the turn's ephemeral blocks (recent conversation, prior action facts) follow
    it so they stay out of the cacheable system prompt while still being the
    part an over-budget turn drops first. Read between the delimiters rather
    than assuming the envelope spans the string.
    """
    raw = str(messages[-1].get("content", "")) if messages else ""
    prefix = "USER MESSAGE (literal): <<<"
    suffix = ">>>"
    start = raw.find(prefix)
    if start == -1:
        return raw
    body_at = start + len(prefix)
    end = raw.find(suffix, body_at)
    return raw[body_at:end] if end != -1 else raw


# ``execute_shell_command`` drives ``subprocess.Popen`` (not ``run``) so ESC can
# cancel a child; tests fake the process object instead of the finished result.
_EXPECTED_POPEN_KWARGS: dict[str, object] = {
    "stdout": subprocess.PIPE,
    "stderr": subprocess.PIPE,
    "text": True,
    "encoding": "utf-8",
    "errors": "replace",
    "start_new_session": True,
}


class _FakeProcess:
    """Minimal ``Popen`` stand-in: piped output, exit code, optional hang until terminated."""

    def __init__(self, *, stdout: str, stderr: str, returncode: int, hang: bool) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        # ``None`` so group signalling cannot target a real pid and falls
        # back to ``terminate()``.
        self.pid = None
        self.returncode: int | None = None if hang else returncode
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        if self.returncode is None:
            self.returncode = -15
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9


def _install_fake_popen(
    monkeypatch: object,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    hang: bool = False,
) -> list[tuple[list[str], dict[str, object]]]:
    """Replace ``Popen`` in the executor and return the recorded ``(argv, kwargs)`` calls."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        calls.append((command, kwargs))
        return _FakeProcess(stdout=stdout, stderr=stderr, returncode=returncode, hang=hang)

    monkeypatch.setattr(shell_execution.subprocess, "Popen", _fake_popen)  # type: ignore[attr-defined]
    return calls


def _force_watch_timeout(proc: object, **_kwargs: object) -> SubprocessWatchResult:
    """Watcher stand-in: treat the child as timed out and stop it."""
    terminate = getattr(proc, "terminate", None)
    if callable(terminate):
        terminate()
    return SubprocessWatchResult(
        timed_out=True,
        cancelled=False,
        exit_code=getattr(proc, "returncode", -15),
        terminated_by_watcher=True,
    )


def _expected_shell_argv(command: str) -> list[str]:
    if shell_execution.os.name == "nt":
        shell = shell_execution.os.environ.get("COMSPEC") or "cmd.exe"
        return [shell, "/d", "/s", "/c", command]
    shell = shell_execution.os.environ.get("SHELL") or "/bin/sh"
    return [shell, "-lc", command]


class _MessageMappedActionLLM(FakeActionLLM):
    def __init__(self) -> None:
        super().__init__([])

    def invoke(
        self,
        messages: list[dict[str, object]],
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, object]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        self.invocations += 1
        message = _message_from_agent_prompt(messages)
        actions, _has_unhandled = _FAKE_PLANS.get(message, ([], False))
        return _response_from_actions(list(actions))


# Deterministic phrase -> (planned actions, has_unhandled_clause) mapping used by the
# fake LLM planner. Reconstructed from each execution test's own assertions and the
# documented phrase mappings of the (now-removed) deterministic mapper.
#
# Semantics enforced by the action-agent path (v0.1 has NO planning-stage
# fail-closed denial — every terminal action is read-only):
#   - ([], *)                -> fall through to chat (handled is False, no history).
#   - ([...], *)             -> execute the listed (non-handoff) actions; the
#                               has_unhandled flag is ignored, so an unmapped clause
#                               never blocks the matched actions from running.
_FAKE_PLANS: dict[str, tuple[list[PlannedAction], bool]] = {
    "check the health of my opensre and then show me all connected services": (
        [_action("slash", "/health"), _action("slash", "/integrations list")],
        False,
    ),
    "switch from the current ollama model to setting the model to anthropic": (
        [_action("llm_provider", "anthropic")],
        False,
    ),
    "please implement /history search": (
        [_action("implementation", "/history search")],
        False,
    ),
    (
        "tell me about what the discord integration can do and then tell me what "
        "datadog services I have connections to"
    ): (
        [_action("slash", "/integrations show datadog")],
        True,
    ),
    (
        "tell me how you are doing AND show me all the services we are connected to "
        "AND then deploy OpenSRE to EC2"
    ): (
        [_action("slash", "/integrations list"), _action("slash", "/remote")],
        True,
    ),
    (
        "tell me which services are connected AND then tell me the current CLI version "
        "AND then deploy to EC2 within 90 seconds"
    ): (
        [
            _action("slash", "/integrations list"),
            _action("slash", "/version"),
            _action("slash", "/remote"),
        ],
        False,
    ),
    "show me connected services and sing a song": (
        [_action("slash", "/integrations list")],
        True,
    ),
    # Shell phrases — the planner emits the exact command body for the shell tool.
    "run `pwd`": ([_action("shell", "pwd")], False),
    r"run `cd C:\Users\Alice`": ([_action("shell", r"cd C:\Users\Alice")], False),
    r"run `CD C:\Users\Alice`": ([_action("shell", r"CD C:\Users\Alice")], False),
    r"run `cd C:\`": ([_action("shell", "cd C:\\")], False),
    r'run `cd "C:\Users\Alice"`': ([_action("shell", r'cd "C:\Users\Alice"')], False),
    "execute false": ([_action("shell", "false")], False),
    "run `true`": ([_action("shell", "true")], False),
    "run `!echo hello`": ([_action("shell", "!echo hello")], False),
    "run `!cd /tmp`": ([_action("shell", "!cd /tmp")], False),
    "run `!pwd`": ([_action("shell", "!pwd")], False),
    "run `sudo rm -rf /tmp/demo`": ([_action("shell", "sudo rm -rf /tmp/demo")], False),
    "run `ls | wc -l`": ([_action("shell", "ls | wc -l")], False),
    'run cat "/tmp/file with spaces.txt"': (
        [_action("shell", 'cat "/tmp/file with spaces.txt"')],
        False,
    ),
    'run `cat "/tmp/file with spaces.txt"`': (
        [_action("shell", 'cat "/tmp/file with spaces.txt"')],
        False,
    ),
    'run `cat "unterminated`': (
        [_action("shell", 'cat "unterminated')],
        False,
    ),
}


def _llm_response(
    actions: list[PlannedAction],
    *,
    has_unhandled: bool = False,  # noqa: ARG001
) -> AgentLLMResponse:
    return _response_from_actions(actions)


@pytest.fixture(autouse=True)
def _llm_planner_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_action_llm_factory(monkeypatch, _MessageMappedActionLLM)


def test_execute_cli_actions_dispatches_planned_commands(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)

    session = Session()
    console, buf = _capture()
    handled = action_turn.run_action_tool_turn(
        "check the health of my opensre and then show me all connected services",
        session,
        console,
    )

    assert handled.handled is True
    assert dispatched == ["/health", "/integrations list"]
    assert session.history == [
        {"type": "slash", "text": "/health", "ok": True},
        {"type": "slash", "text": "/integrations list", "ok": True},
    ]
    output = buf.getvalue()
    assert "Requested actions" not in output
    assert "1. command" not in output
    assert "2. command" not in output
    assert "ran /health" in output
    assert "ran /integrations list" in output


def test_execute_cli_actions_skips_remaining_actions_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-action plan: if the user pressed Esc / typed ``/cancel``
    between actions, the per-dispatch cancel event is set on the
    ``StreamingConsole``. The action loop checks ``cancel_requested``
    at the top of each iteration and breaks, so the remaining actions
    in the plan are NOT dispatched.

    Pre-fix, the loop ran every action regardless of cancel state, so
    cancelling a "do A then B" plan still ran B even after the user
    explicitly asked to stop. This pins the new contract that an
    in-flight cancel halts the plan after the current action.
    """
    dispatched: list[str] = []

    class _CancelAfterFirst:
        """Console-shaped object that returns ``cancel_requested=True``
        only AFTER the first action has been dispatched, simulating
        the user hitting Esc / typing ``/cancel`` between actions."""

        def __init__(self, inner: Console, dispatched: list[str]) -> None:
            self._inner = inner
            self._dispatched = dispatched

        @property
        def cancel_requested(self) -> bool:
            return len(self._dispatched) >= 1

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)

    session = Session()
    inner_console, buf = _capture()
    console = _CancelAfterFirst(inner_console, dispatched)
    handled = action_turn.run_action_tool_turn(
        "check the health of my opensre and then show me all connected services",
        session,
        console,  # type: ignore[arg-type]
    )

    # A cancelled turn reports ``cancelled``; the orchestrator short-circuits on
    # that flag to skip gather/answer, which is what forcing ``handled=True``
    # used to accomplish. ``handled`` now reflects the work that actually ran.
    assert handled.cancelled is True
    assert handled.handled is False
    # Only the first action ran; the second was skipped because the
    # cancel event was set between iterations.
    assert dispatched == ["/health"], (
        f"second action ran despite cancel between iterations: {dispatched}"
    )
    output = buf.getvalue()
    assert "ran /health" in output
    assert "ran /integrations list" not in output
    assert "remaining actions cancelled" in output


def test_execute_cli_actions_falls_through_for_local_llama_request(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)

    session = Session()
    console, _ = _capture()
    handled = action_turn.run_action_tool_turn("please connect to local llama", session, console)

    assert handled.handled is False
    assert dispatched == []
    assert session.history == []


def test_execute_cli_actions_switches_llm_provider(monkeypatch: object) -> None:
    switches: list[str] = []

    def _fake_switch(provider: str, console: Console, model: str | None = None) -> bool:
        assert model is None
        switches.append(provider)
        console.print(f"switched to {provider}")
        return True

    monkeypatch.setattr(
        llm_provider_adapter,
        "switch_llm_provider",
        _fake_switch,
    )

    session = Session()
    console, buf = _capture()
    handled = action_turn.run_action_tool_turn(
        "switch from the current ollama model to setting the model to anthropic",
        session,
        console,
    )

    assert handled.handled is True
    assert switches == ["anthropic"]
    assert session.history == [
        {"type": "slash", "text": "/model set anthropic", "ok": True},
    ]
    output = buf.getvalue()
    assert "$ /model set anthropic" in output
    assert "switched to anthropic" in output


def test_execute_cli_actions_records_llm_provider_failure(monkeypatch: object) -> None:
    def _fake_switch(provider: str, console: Console, model: str | None = None) -> bool:
        assert provider == "anthropic"
        assert model is None
        console.print("missing credential")
        return False

    monkeypatch.setattr(
        llm_provider_adapter,
        "switch_llm_provider",
        _fake_switch,
    )

    session = Session()
    console, _ = _capture()
    handled = action_turn.run_action_tool_turn(
        "switch from the current ollama model to setting the model to anthropic",
        session,
        console,
    )

    assert handled.handled is True
    assert session.history[-1] == {"type": "slash", "text": "/model set anthropic", "ok": False}


def test_execute_cli_actions_sets_bare_model_for_active_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reasoning_models: list[str] = []

    _patch_action_llm_factory(
        monkeypatch,
        lambda: FakeActionLLM(
            [
                _llm_response(
                    [
                        PlannedAction(
                            kind="llm_provider",
                            content="gpt-5.5",
                            position=0,
                            source="llm",
                            target_surface="slash",
                        )
                    ]
                )
            ]
        ),
    )
    monkeypatch.setattr(
        llm_provider_adapter,
        "switch_reasoning_model",
        lambda model, console: (
            reasoning_models.append(model),
            console.print(model),
            True,
        )[2],
    )

    session = Session()
    console, buf = _capture()
    handled = action_turn.run_action_tool_turn("switch model to gpt 5.5", session, console)

    assert handled.handled is True
    assert reasoning_models == ["gpt-5.5"]
    assert session.history[-1] == {"type": "slash", "text": "/model set gpt-5.5", "ok": True}
    assert "$ /model set gpt-5.5" in buf.getvalue()


def test_execute_cli_actions_runs_implementation_action(monkeypatch: object) -> None:
    calls: list[str] = []

    def _fake_run_implementation(request: str, presenter: object) -> None:
        calls.append(request)
        presenter.session.record("implementation", request, ok=True)  # type: ignore[attr-defined]
        presenter.console.print(f"implemented {request}")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "tools.interactive_shell.actions.implementation.run_claude_code_implementation",
        _fake_run_implementation,
    )

    session = Session()
    console, buf = _capture()
    handled = action_turn.run_action_tool_turn("please implement /history search", session, console)

    assert handled.handled is True
    assert calls == ["/history search"]
    assert session.history == [
        {"type": "implementation", "text": "/history search", "ok": True},
    ]
    output = buf.getvalue()
    assert "Requested actions" not in output
    assert "implemented /history search" in output


def test_execute_cli_actions_answers_discord_then_dispatches_datadog(
    monkeypatch: object,
) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)

    session = Session()
    console, buf = _capture()
    handled = action_turn.run_action_tool_turn(
        (
            "tell me about what the discord integration can do and then tell me what "
            "datadog services I have connections to"
        ),
        session,
        console,
    )

    # v0.1 has no planning-stage denial: the matched clause runs and the
    # unmappable "tell me about discord" clause is simply dropped.
    assert handled.handled is True
    assert dispatched == ["/integrations show datadog"]
    assert session.history == [
        {"type": "slash", "text": "/integrations show datadog", "ok": True},
    ]
    output = buf.getvalue()
    assert "ran /integrations show datadog" in output
    assert "couldn't safely decide actions" not in output.lower()


def test_compound_prompt_executes_all_supported_tasks(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)

    session = Session()
    console, buf = _capture()
    handled = action_turn.run_action_tool_turn(
        (
            "tell me how you are doing AND show me all the services we are connected to "
            "AND then deploy OpenSRE to EC2"
        ),
        session,
        console,
    )

    # The two executable clauses run; the chatty "tell me how you are doing"
    # clause is dropped without failing the turn closed.
    assert handled.handled is True
    assert dispatched == ["/integrations list", "/remote"]
    assert session.history == [
        {"type": "slash", "text": "/integrations list", "ok": True},
        {"type": "slash", "text": "/remote", "ok": True},
    ]
    output = buf.getvalue()
    assert "ran /integrations list" in output
    assert "ran /remote" in output
    assert "couldn't safely decide actions" not in output.lower()


def test_services_version_deploy_prompt_executes_in_order(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)

    session = Session()
    console, buf = _capture()
    handled = action_turn.run_action_tool_turn(
        (
            "tell me which services are connected AND then tell me the current CLI version "
            "AND then deploy to EC2 within 90 seconds"
        ),
        session,
        console,
    )

    assert handled.handled is True
    assert dispatched == ["/integrations list", "/version", "/remote"]
    output = buf.getvalue()
    assert output.index("ran /integrations list") < output.index("ran /version")
    assert "EC2 deployment creates AWS" not in output


def test_partial_match_executes_matched_clause_and_drops_unhandled(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)

    session = Session()
    console, buf = _capture()

    # "sing a song" is chatty filler; v0.1 drops it and still runs the matched
    # "/integrations list" clause instead of failing the whole turn closed.
    assert action_turn.run_action_tool_turn(
        "show me connected services and sing a song", session, console
    ).handled
    assert dispatched == ["/integrations list"]
    output = buf.getvalue()
    assert "ran /integrations list" in output
    assert "couldn't safely decide actions" not in output.lower()


def test_execute_cli_actions_falls_through_for_chat() -> None:
    session = Session()
    console, _ = _capture()

    assert action_turn.run_action_tool_turn("hey", session, console).handled is False
    assert session.history == []


def test_execute_cli_actions_runs_shell_command(monkeypatch: object) -> None:
    def _fake_cwd(_: type[Path]) -> PurePosixPath:
        return PurePosixPath("/tmp/project")

    def _fail_run(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for pwd")

    monkeypatch.setattr(subprocess_runner.Path, "cwd", classmethod(_fake_cwd))
    monkeypatch.setattr(shell_execution.subprocess, "run", _fail_run)

    session = Session()
    console, buf = _capture()

    assert action_turn.run_action_tool_turn("run `pwd`", session, console).handled is True
    assert session.history == [
        {"type": "shell", "text": "pwd", "ok": True},
    ]
    output = buf.getvalue()
    assert "$ pwd" in output
    assert "/tmp/project" in output


def test_execute_cli_actions_cd_preserves_windows_paths(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(platform_module, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_runner.os, "chdir", _fake_chdir)

    session = Session()
    console, _ = _capture()

    message = r"run `cd C:\Users\Alice`"
    assert action_turn.run_action_tool_turn(message, session, console).handled is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "shell", "text": r"cd C:\Users\Alice", "ok": True},
    ]


def test_execute_cli_actions_cd_dispatches_case_insensitively(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    def _fail_run(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for CD")

    monkeypatch.setattr(platform_module, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_runner.os, "chdir", _fake_chdir)
    monkeypatch.setattr(shell_execution.subprocess, "run", _fail_run)

    session = Session()
    console, _ = _capture()

    message = r"run `CD C:\Users\Alice`"
    assert action_turn.run_action_tool_turn(message, session, console).handled is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "shell", "text": r"CD C:\Users\Alice", "ok": True},
    ]


def test_execute_cli_actions_cd_handles_trailing_backslash_on_windows(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(platform_module, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_runner.os, "chdir", _fake_chdir)

    session = Session()
    console, _ = _capture()

    message = r"run `cd C:\`"
    assert action_turn.run_action_tool_turn(message, session, console).handled is True
    assert changed_directories == [Path("C:\\")]
    assert session.history == [
        {"type": "shell", "text": "cd C:\\", "ok": True},
    ]


def test_execute_cli_actions_cd_strips_quotes_on_windows(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(platform_module, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_runner.os, "chdir", _fake_chdir)

    session = Session()
    console, _ = _capture()

    message = r'run `cd "C:\Users\Alice"`'
    assert action_turn.run_action_tool_turn(message, session, console).handled is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "shell", "text": r'cd "C:\Users\Alice"', "ok": True},
    ]


def test_execute_cli_actions_records_shell_failure(monkeypatch: object) -> None:
    calls = _install_fake_popen(monkeypatch, stderr="nope\n", returncode=2)

    session = Session()
    console, buf = _capture()

    assert action_turn.run_action_tool_turn("execute false", session, console).handled is True
    assert calls == [(["false"], _EXPECTED_POPEN_KWARGS)]
    assert session.history[-1] == {
        "type": "shell",
        "text": "false",
        "ok": False,
        "response_text": "nope\n✗ exit 2",
    }
    output = buf.getvalue()
    assert "nope" in output
    assert "exit 2" in output


def test_execute_cli_actions_shell_command_times_out(monkeypatch: object) -> None:
    _install_fake_popen(monkeypatch, stdout="partial out\n", stderr="partial err\n", hang=True)
    monkeypatch.setattr(
        "tools.interactive_shell.shell.execution.watch_subprocess_until_exit",
        _force_watch_timeout,
    )

    session = Session()
    console, buf = _capture()

    assert action_turn.run_action_tool_turn("run `true`", session, console).handled is True
    assert session.history[-1] == {
        "type": "shell",
        "text": "true",
        "ok": False,
        "response_text": "command timed out after 120 seconds",
    }
    output = buf.getvalue().lower()
    assert "timed out" in output
    assert "partial out" in output
    assert "partial err" in output


def test_execute_cli_actions_runs_passthrough_with_shell_true(monkeypatch: object) -> None:
    calls = _install_fake_popen(monkeypatch, stdout="ok\n")

    session = Session()
    console, buf = _capture()

    assert action_turn.run_action_tool_turn("run `!echo hello`", session, console).handled is True
    assert calls == [(_expected_shell_argv("echo hello"), _EXPECTED_POPEN_KWARGS)]
    assert session.history[-1] == {
        "type": "shell",
        "text": "!echo hello",
        "ok": True,
        "response_text": "ok",
    }
    output = buf.getvalue()
    assert "explicit shell passthrough enabled" in output
    assert "ok" in output


def test_execute_cli_actions_dispatches_bang_cd_through_builtin(monkeypatch: object) -> None:
    dirs: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        dirs.append(target)

    def _boom(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for !cd builtin execution")

    monkeypatch.setattr(subprocess_runner.os, "chdir", _fake_chdir)
    monkeypatch.setattr(shell_execution.subprocess, "run", _boom)

    session = Session()
    console, buf = _capture()

    message = "run `!cd /tmp`"
    assert action_turn.run_action_tool_turn(message, session, console).handled is True
    assert dirs == [Path("/tmp")]
    assert session.history[-1] == {"type": "shell", "text": "cd /tmp", "ok": True}
    captured = buf.getvalue()
    assert "explicit shell passthrough enabled" not in captured


def test_execute_cli_actions_dispatches_bang_pwd_through_builtin(monkeypatch: object) -> None:
    def _fake_cwd(_: type[Path]) -> PurePosixPath:
        return PurePosixPath("/shown")

    def _boom(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for !pwd builtin execution")

    monkeypatch.setattr(subprocess_runner.Path, "cwd", classmethod(_fake_cwd))
    monkeypatch.setattr(shell_execution.subprocess, "run", _boom)

    session = Session()
    console, buf = _capture()

    assert action_turn.run_action_tool_turn("run `!pwd`", session, console).handled is True
    assert session.history[-1] == {"type": "shell", "text": "pwd", "ok": True}
    captured = buf.getvalue()
    assert "/shown" in captured
    assert "explicit shell passthrough enabled" not in captured


def test_execute_cli_actions_handles_path_with_spaces_run_phrase() -> None:
    session = Session()
    console, buf = _capture()
    result = action_turn.run_action_tool_turn(
        'run cat "/tmp/file with spaces.txt"', session, console
    )
    assert result.handled is True
    assert session.history[-1]["type"] == "shell"
    output = buf.getvalue()
    assert "/tmp/file with spaces.txt" in output


def test_execute_cli_actions_backtick_shell_preserves_space_path_token(monkeypatch: object) -> None:
    calls = _install_fake_popen(monkeypatch, stdout="done\n")

    session = Session()
    console, _ = _capture()

    assert (
        action_turn.run_action_tool_turn(
            'run `cat "/tmp/file with spaces.txt"`', session, console
        ).handled
        is True
    )
    # On Windows, shlex with posix=False preserves quotes for tokens with spaces.
    # Both Windows and Posix parsers correctly strip outer quotes from tokens
    # following the policy.py _strip_outer_quotes logic.
    expected_path = "/tmp/file with spaces.txt"
    assert calls[0][0] == ["cat", expected_path]


def test_execute_cli_actions_counts_planned_and_executed(monkeypatch: object) -> None:
    captured_planned: list[tuple[int, bool]] = []
    captured_executed: list[tuple[int, int, int]] = []

    monkeypatch.setattr(
        "infrastructure.analytics.capture.capture_terminal_actions_planned",
        lambda *, planned_count, has_unhandled_clause: captured_planned.append(
            (planned_count, has_unhandled_clause)
        ),
    )
    monkeypatch.setattr(
        "infrastructure.analytics.capture.capture_terminal_actions_executed",
        lambda *, planned_count, executed_count, executed_success_count: captured_executed.append(
            (planned_count, executed_count, executed_success_count)
        ),
    )

    session = Session()
    console, _ = _capture()
    # Analytics now fire from ShellTurnAccounting inside run_harness_turn,
    # not from run_action_tool_turn directly. Drive the full turn with a no-op
    # answer agent so no real LLM is invoked.
    result = run_harness_turn(
        "run `pwd`",
        session,
        console,
        recorder=None,
    )

    action_result = result.action_result
    assert action_result.handled is True
    assert action_result.planned_count == 1
    assert action_result.executed_count == 1
    assert action_result.executed_success_count == 1
    assert captured_planned == [(1, False)]
    assert captured_executed == [(1, 1, 1)]


def test_execute_cli_actions_persists_action_agent_llm_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> object:
        raise RuntimeError("action agent unavailable")

    _patch_action_llm_factory(monkeypatch, _raise)

    session = Session()
    console, buf = _capture()
    handled = action_turn.run_action_tool_turn("check health", session, console)

    assert handled.handled is True
    assert handled.has_unhandled_clause is True
    assert session.history == [{"type": "cli_agent", "text": "check health", "ok": False}]
    assert session.cli_agent_messages[-1] == ("assistant", "action agent unavailable")
    output = buf.getvalue()
    assert "couldn't safely decide actions" not in output.lower()
    assert "action agent unavailable" in output


def test_execute_cli_actions_propagates_credit_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.llm.shared.llm_retry import OpenSRECreditsExhaustedError

    exhausted = OpenSRECreditsExhaustedError(
        "OpenSRE hosted credits are exhausted.",
        upgrade_url="https://app.opensre.test/usage",
    )

    def _raise() -> object:
        raise exhausted

    _patch_action_llm_factory(monkeypatch, _raise)

    session = Session()
    console, _ = _capture()
    with pytest.raises(OpenSRECreditsExhaustedError) as exc_info:
        action_turn.run_action_tool_turn("check health", session, console)

    assert exc_info.value is exhausted


def test_execute_cli_actions_executes_matched_clause_ignoring_unhandled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_action_llm_factory(
        monkeypatch,
        lambda: FakeActionLLM([_llm_response([_action("slash", "/health")], has_unhandled=True)]),
    )

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)

    captured_planned: list[tuple[int, bool]] = []
    captured_executed: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        "infrastructure.analytics.capture.capture_terminal_actions_planned",
        lambda *, planned_count, has_unhandled_clause: captured_planned.append(
            (planned_count, has_unhandled_clause)
        ),
    )
    monkeypatch.setattr(
        "infrastructure.analytics.capture.capture_terminal_actions_executed",
        lambda *, planned_count, executed_count, executed_success_count: captured_executed.append(
            (planned_count, executed_count, executed_success_count)
        ),
    )

    session = Session()
    console, _ = _capture()
    # Analytics now fire from ShellTurnAccounting inside run_harness_turn.
    result = run_harness_turn(
        "check health",
        session,
        console,
        recorder=None,
    )

    # The unhandled flag no longer denies the turn: the matched /health runs.
    action_result = result.action_result
    assert action_result.handled is True
    assert action_result.planned_count == 1
    assert action_result.executed_count == 1
    assert action_result.executed_success_count == 1
    assert action_result.has_unhandled_clause is False
    assert captured_planned == [(1, False)]
    assert captured_executed == [(1, 1, 1)]


def test_execute_cli_actions_bang_prefix_uses_only_explicit_shell_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare !cmd input is the only direct shell AgentTool escape."""
    llm_called: list[str] = []

    def _fail_if_called() -> None:  # pragma: no cover
        llm_called.append("called")
        raise AssertionError("LLM planner must not be called for !cmd input")

    _patch_action_llm_factory(monkeypatch, _fail_if_called)

    calls = _install_fake_popen(monkeypatch, stdout="ok\n")

    session = Session()
    console, buf = _capture()

    # Multiline !cmd with internal whitespace — the exact shape the user types.
    handled = action_turn.run_action_tool_turn("!curl\n      wttr.in/London", session, console)

    assert handled.handled is True
    assert llm_called == []
    assert session.history[-1] == {
        "type": "shell",
        "text": "!curl wttr.in/London",
        "ok": True,
        "response_text": "ok",
    }
    # The executor strips `!` and invokes the user's shell as argv, never shell=True.
    assert calls[0][0] == _expected_shell_argv("curl wttr.in/London")
    assert "shell" not in calls[0][1]
    assert "explicit shell passthrough enabled" in buf.getvalue()


def test_execute_cli_actions_bang_prefix_single_line_dispatches_to_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-line !cmd shell execution uses the explicit shell escape."""
    llm_called: list[str] = []

    def _fail_if_called() -> None:  # pragma: no cover
        llm_called.append("called")
        raise AssertionError("LLM planner must not be called for !cmd input")

    _patch_action_llm_factory(monkeypatch, _fail_if_called)

    calls = _install_fake_popen(monkeypatch, stdout="out\n")

    session = Session()
    console, _ = _capture()

    handled = action_turn.run_action_tool_turn("!echo hello world", session, console)

    assert handled.handled is True
    assert llm_called == []
    assert session.history[-1] == {
        "type": "shell",
        "text": "!echo hello world",
        "ok": True,
        "response_text": "out",
    }
    assert calls[0][0] == _expected_shell_argv("echo hello world")
    assert "shell" not in calls[0][1]
