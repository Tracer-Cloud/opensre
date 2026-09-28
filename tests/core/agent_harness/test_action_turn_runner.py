"""Characterization of ``ActionTurnRunner`` and ``HeadlessAgent`` turn seams.

Pins the developer API after the nested-``def`` / long-kwargs cleanup:
the surface owns a runner, ``dispatch`` wires bound methods into ``run_turn``,
and ``bind_turn`` refreshes the runner when output or hooks change.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.agent_harness.runtime import ActionTurnRunner, TurnBinding, default_llm_factory
from core.agent_harness.turns.headless_adapters import BufferOutputSink, NullToolProvider
from core.agent_harness.turns.headless_agent import HeadlessAgent
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.llm_invoke_errors import ProviderFailureKind, classify_llm_provider_failure
from core.tool.execution import ToolExecutionHooks
from infrastructure.turn_host.cancel_console import CancelConsole
from surfaces.interactive_shell.runtime.turn_seams import bind_injected_stages
from surfaces.interactive_shell.session import Session


def _handled_action(text: str, **_kwargs: Any) -> ToolCallingTurnResult:
    _ = text
    return ToolCallingTurnResult(0, 0, 0, False, True)


def test_action_turn_runner_requires_llm_factory_at_construction() -> None:
    """Issue #5235: a missing llm_factory must fail loudly at construction
    (the "composition seam"), not silently mid-turn via a hidden fallback.
    The error message must classify as NOT_CONFIGURED so existing provider-
    failure rendering/remediation picks it up correctly."""
    with pytest.raises(ValueError) as exc_info:
        ActionTurnRunner(
            output=BufferOutputSink(),
            tools=NullToolProvider(),
            llm_factory=None,  # type: ignore[arg-type]
        )

    assert classify_llm_provider_failure(str(exc_info.value)) is ProviderFailureKind.NOT_CONFIGURED


def test_action_turn_runner_accepts_a_real_default_llm_factory() -> None:
    """The composition-root default (core.agent_harness.llm_resolution.default_llm_factory)
    must satisfy the required field without raising at construction."""
    ActionTurnRunner(
        output=BufferOutputSink(),
        tools=NullToolProvider(),
        llm_factory=default_llm_factory,
    )


def test_action_turn_runner_is_exported_from_runtime_not_the_root() -> None:
    from core import agent_harness as pkg
    from core.agent_harness import runtime

    assert runtime.ActionTurnRunner is ActionTurnRunner
    assert not hasattr(pkg, "ActionTurnRunner")
    assert not hasattr(runtime, "run_action_turn")
    assert not hasattr(runtime, "ActionRequest")
    assert not hasattr(runtime, "execute_action_agent_turn")


def test_action_turn_runner_run_accepts_confirm_fn(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(message: str, session: Any, args: Any) -> ToolCallingTurnResult:
        captured["message"] = message
        captured["confirm_fn"] = args.confirm_fn
        captured["is_tty"] = args.is_tty
        return ToolCallingTurnResult(0, 0, 0, False, False)

    monkeypatch.setattr(
        "core.agent_harness.turns.action_driver._run_action_turn",
        _fake_run,
    )

    def _confirm(_prompt: str) -> str:
        return "y"

    def _unused_llm_factory() -> Any:
        raise AssertionError("llm_factory should not be called; _run_action_turn is mocked")

    result = ActionTurnRunner(
        output=BufferOutputSink(),
        tools=NullToolProvider(),
        llm_factory=_unused_llm_factory,
    ).run("hi", Session(), confirm_fn=_confirm, is_tty=False)

    assert result.handled is False
    assert captured["message"] == "hi"
    assert captured["confirm_fn"] is _confirm
    assert captured["is_tty"] is False


def test_headless_agent_uses_bound_methods_not_nested_defs() -> None:
    source = inspect.getsource(HeadlessAgent.dispatch)
    assert "def execute_actions" not in source
    assert "def answer" not in source
    assert "def gather" not in source
    assert "self._execute_actions" in source


def test_bind_turn_rebuilds_action_runner_when_output_changes() -> None:
    first = BufferOutputSink()
    second = BufferOutputSink()
    agent = InMemoryHeadlessBuild(output=first).agent(tools=NullToolProvider())
    before = agent._action_runner  # noqa: SLF001
    assert before.output is first

    agent.bind_turn(TurnBinding(output=second))
    after = agent._action_runner  # noqa: SLF001
    assert after is not before
    assert after.output is second


def test_bind_turn_rebuilds_action_runner_when_tool_hooks_change() -> None:
    hooks = ToolExecutionHooks()
    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    before = agent._action_runner  # noqa: SLF001

    agent.bind_turn(TurnBinding(tool_hooks=hooks))
    after = agent._action_runner  # noqa: SLF001
    assert after is not before
    assert after.tool_hooks is hooks


def test_bind_turn_keeps_runner_when_only_accounting_changes() -> None:
    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    before = agent._action_runner  # noqa: SLF001
    agent.bind_turn(TurnBinding(accounting=MagicMock()))
    assert agent._action_runner is before  # noqa: SLF001


def test_harness_turn_driver_adds_no_stage_of_its_own() -> None:
    import io

    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
    from tests.shared import harness_turn_driver

    source = inspect.getsource(harness_turn_driver.run_harness_turn)
    assert "build_shell_agent" in source
    assert "_ShellTurnBindings" not in source
    assert "ShellActionRunner" not in source

    agent = build_shell_agent(Session(), Console(file=io.StringIO(), force_terminal=False))
    bind_injected_stages(
        agent,
        Session(),
        Console(file=io.StringIO()),
        BufferOutputSink(),
        execute_actions=None,
        request_exit=None,
        tool_hooks=None,
    )
    assert agent._execute_actions_override is None  # noqa: SLF001


def test_shell_agent_keeps_core_runner_across_console_rebind() -> None:
    import threading

    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent

    first = Console(file=MagicMock())
    second = Console(file=MagicMock())
    agent = build_shell_agent(Session(), first)
    before = agent._action_runner  # noqa: SLF001

    agent.bind_turn(TurnBinding(console=CancelConsole(second, threading.Event())))
    after = agent._action_runner  # noqa: SLF001

    assert after is before


def test_shell_agent_rebuilds_runner_when_external_output_changes() -> None:
    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent

    agent = build_shell_agent(Session(), Console(file=MagicMock()))
    before = agent._action_runner  # noqa: SLF001

    agent.bind_turn(TurnBinding(output=BufferOutputSink()))
    after = agent._action_runner  # noqa: SLF001

    assert after is not before


def test_a_sink_without_hooks_clears_the_previous_sinks_hooks() -> None:
    """A pooled agent must not carry approval hooks between conversations.

    The gateway reuses agents from a pool and binds each turn with
    ``tool_hooks=getattr(sink, "tool_hooks", None)``. A sink with no hooks
    therefore passes ``None``. If that were read as "leave them alone", the
    previous sink's approval callback would stay attached and later tool calls
    would send approval controls to the wrong conversation — or wait on a reply
    that can never arrive.
    """
    # Arrange
    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    approval_hooks = MagicMock()
    agent.bind_turn(TurnBinding(tool_hooks=approval_hooks))
    assert agent._tool_hooks is approval_hooks  # noqa: SLF001

    # Act: the next turn's sink has no hooks.
    agent.bind_turn(TurnBinding(tool_hooks=None))

    # Assert
    assert agent._tool_hooks is None  # noqa: SLF001


def test_a_binding_states_the_whole_turn_and_replace_carries_it_forward() -> None:
    """A ``TurnBinding`` replaces the previous turn's values wholesale.

    Nothing accumulates from an earlier binding — hooks not in this turn's
    binding are gone. A host that rebinds mid-turn (the shell swaps accounting
    per goal-loop iteration) does so with ``replace(binding, …)``, which
    carries the turn's hooks and callback forward by construction.
    """
    from dataclasses import replace

    # Arrange
    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    approval_hooks = MagicMock()
    confirm = MagicMock()
    binding = TurnBinding(tool_hooks=approval_hooks, confirm_fn=confirm, is_tty=True)
    agent.bind_turn(binding)

    # Act — a mid-turn rebind derived from the same binding keeps everything;
    # a fresh binding without hooks drops them.
    agent.bind_turn(replace(binding, accounting=MagicMock()))
    kept = (agent._tool_hooks, agent._confirm_fn, agent._is_tty)  # noqa: SLF001
    agent.bind_turn(TurnBinding(output=BufferOutputSink()))

    # Assert
    assert kept == (approval_hooks, confirm, True)
    assert agent._tool_hooks is None  # noqa: SLF001
    assert agent._confirm_fn is None  # noqa: SLF001


def test_long_lived_shell_agent_receives_each_turns_confirm_fn_and_tty() -> None:
    """A rebound turn's ``confirm_fn`` / ``is_tty`` reach the action stage.

    The REPL builds its agent once at startup (no confirm_fn) and rebinds it per
    turn. If the turn's confirmation callback were not rebound, a mutating
    ``cli_exec`` would fall back to blocking ``input()`` while prompt_toolkit
    owns stdin.
    """
    import io

    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
    from tests.shared.harness_turn_driver import run_harness_turn

    # Arrange — agent built like the controller does: no confirm_fn, no is_tty.
    seen: list[tuple[Any, Any]] = []

    def _confirm(_prompt: str) -> str:
        return "y"

    def _spy_execute(
        message: str, session: Any, console: Any, **kwargs: Any
    ) -> ToolCallingTurnResult:
        _ = (message, session, console)
        seen.append((kwargs.get("confirm_fn"), kwargs.get("is_tty")))
        return ToolCallingTurnResult(0, 0, 0, False, True)

    console = Console(file=io.StringIO(), force_terminal=False)
    agent = build_shell_agent(Session(), console)

    # Act — one turn on the long-lived agent, with this turn's confirm_fn / tty.
    run_harness_turn(
        "run something",
        Session(),
        console,
        confirm_fn=_confirm,
        is_tty=True,
        agent=agent,
        execute_actions=_spy_execute,
    )

    # Assert — the action stage saw the turn's callback, not the construction-time None.
    assert seen == [(_confirm, True)]


def test_a_stage_injected_on_one_turn_does_not_carry_into_the_next() -> None:
    """Injected seams are stated whole per turn, like ``TurnBinding``.

    On the long-lived REPL agent, a fake stage from an earlier turn must not
    stay active when a later turn omits it — omission means "the agent's own
    stage", never "whatever was bound before".
    """
    import io

    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
    from tests.shared.harness_turn_driver import run_harness_turn

    # Arrange — a long-lived agent; turn 1 injects a fake action stage.
    console = Console(file=io.StringIO(), force_terminal=False)
    agent = build_shell_agent(Session(), console)

    def _fake_execute(
        message: str, session: Any, console: Any, **_kw: Any
    ) -> ToolCallingTurnResult:
        _ = (message, session, console)
        return ToolCallingTurnResult(0, 0, 0, False, True, response_text="fake")

    run_harness_turn(
        "turn one",
        Session(),
        console,
        agent=agent,
        execute_actions=_fake_execute,
    )
    assert agent._execute_actions_override is not None  # noqa: SLF001

    # Act — turn 2 omits the seam; bind only, do not dispatch (needs an LLM).

    bind_injected_stages(
        agent,
        Session(),
        console,
        BufferOutputSink(),
        execute_actions=None,
        request_exit=None,
        tool_hooks=None,
    )

    # Assert — the agent is back on its own action stage.
    assert agent._execute_actions_override is None  # noqa: SLF001


def test_handle_is_the_one_host_loop_binding_each_outer_turn() -> None:
    """``handle`` binds the turn, dispatches, and re-binds accounting per outer turn.

    Gateway and shell call this and nothing else per message; the accounting
    factory is applied per outer turn because a session goal may run several,
    each with different text.
    """
    from dataclasses import replace

    from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild

    # Arrange — an agent whose action stage handles every turn; record what got bound.
    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    seen: list[tuple[str, Any]] = []
    original_bind = agent.bind_turn

    def _spy_bind(binding: TurnBinding) -> None:
        seen.append(("bind", getattr(binding.accounting, "label", None)))
        original_bind(binding)

    def _fake_execute(text: str, *, confirm_fn=None, is_tty=None, turn_plan=None):  # type: ignore[no-untyped-def]
        seen.append(("dispatch", text))
        return ToolCallingTurnResult(0, 0, 0, False, True, response_text="ok")

    agent.bind_turn = _spy_bind  # type: ignore[method-assign]
    agent.bind_stages(execute_actions=_fake_execute)
    hooks = MagicMock()
    binding = TurnBinding(tool_hooks=hooks, is_tty=True)

    def _accounting(message: str) -> Any:
        accounting = MagicMock()
        accounting.label = f"acct:{message}"
        accounting.finalize.side_effect = lambda result: result
        return accounting

    # Act
    result = agent.handle("hello", binding, accounting_factory=_accounting)

    # Assert — one bind (with the factory's accounting) then one dispatch; the
    # rest of the binding (hooks, tty) is carried unchanged.
    assert seen == [("bind", "acct:hello"), ("dispatch", "hello")]
    assert result.action_result is not None
    assert agent._tool_hooks is hooks and agent._is_tty is True  # noqa: SLF001
    assert replace(binding, accounting=None) == binding


def test_one_host_drives_the_agent_and_the_shell_goes_through_it() -> None:
    """There is one host loop, and the shell reaches it rather than repeating it.

    The turn host calls ``agent.handle``; the interactive shell calls the host.
    A shell that grew its own ``agent.handle`` would be a second turn engine —
    exactly what routing it through :class:`TurnRunner` removed.
    """
    import inspect

    import infrastructure.turn_host.turn_runner as turn_host
    from surfaces.interactive_shell.runtime import shell_turn_execution as shell_turn

    host_source = inspect.getsource(turn_host)
    assert "agent.handle(" in host_source
    assert "run_until_session_goal(" not in host_source
    assert "AgentSession(" not in host_source

    shell_source = inspect.getsource(shell_turn)
    assert "handler.run(" in shell_source
    assert "agent.handle(" not in shell_source
    assert "run_until_session_goal(" not in shell_source
    assert "AgentSession(" not in shell_source


def test_handle_runs_the_goal_loop_on_the_session_the_binding_states(monkeypatch: Any) -> None:
    """A rebound session is the goal loop's session, not the previously bound one.

    Gateway resolves a fresh ``SessionCore`` per message; if ``handle`` handed the
    loop the agent's *previous* session, goal state would be read from and
    written to a stale object.
    """
    from core.agent_harness.turns import headless_agent
    from core.agent_harness.turns.headless_adapters import InMemorySessionState
    from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild

    seen: dict[str, Any] = {}

    def _spy_loop(chat: Any, session: Any, message: str, **kwargs: Any) -> Any:
        seen["session"] = session
        return SimpleNamespace(last_result=chat(message))

    monkeypatch.setattr(headless_agent, "run_until_session_goal", _spy_loop)
    previous = InMemorySessionState()
    current = InMemorySessionState()
    agent = InMemoryHeadlessBuild(session=previous).agent(tools=NullToolProvider())
    agent.bind_stages(execute_actions=_handled_action)

    agent.handle("hello", TurnBinding(session=current))

    assert seen["session"] is current


def test_a_second_thread_cannot_start_a_turn_while_one_is_running() -> None:
    """One agent serves one turn at a time; an overlapping turn raises, it does not interleave.

    ``bind_turn`` mutates per-turn state, so two concurrent turns on one agent
    would read each other's binding. The pool's per-session lock keeps this
    from happening in the gateway; the agent now enforces it itself.
    """
    import threading

    from core.agent_harness.runtime import AgentBusyError
    from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild

    # Arrange — an action stage that blocks until the test releases it.
    entered = threading.Event()
    release = threading.Event()

    def _blocking_execute(text: str, **_kw: Any) -> ToolCallingTurnResult:
        entered.set()
        release.wait(timeout=5)
        return ToolCallingTurnResult(0, 0, 0, False, True)

    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    agent.bind_stages(execute_actions=_blocking_execute)
    first = threading.Thread(target=agent.handle, args=("one", TurnBinding()))

    # Act — start turn one, then try turn two while it is mid-flight.
    first.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(AgentBusyError):
            agent.handle("two", TurnBinding())
        with pytest.raises(AgentBusyError):
            agent.dispatch("two")
    finally:
        release.set()
        first.join(timeout=5)

    # Assert — once turn one finished, the agent takes a turn again.
    agent.bind_stages(execute_actions=_handled_action)
    assert agent.handle("three", TurnBinding()) is not None
