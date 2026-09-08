"""Tests for the gateway turn runner."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.runtime import AgentBuildConfig
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from infrastructure.turn_host.session_agents import SessionAgentPool
from infrastructure.turn_host.turn_runner import TurnRunner
from tests.core.agent.orchestration.cross_surface_parity_harness import (
    RecordingTurnOutput,
)
from tests.shared.default_headless_build_stub import default_headless_build_stub
from tests.shared.fake_agent import fake_agent


@pytest.fixture(autouse=True)
def _stub_gateway_turn_analytics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_started", lambda **_: None
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_completed", lambda **_: None
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_failed", lambda **_: None
    )


def _patch_headless_agent(monkeypatch: Any, result: TurnResult) -> MagicMock:
    """Patch the gateway agent factory so construction is inert and dispatch returns ``result``.

    Returns the factory mock. The built agent is ``factory.return_value``; when the
    test needs the real tool provider, read ``factory.return_value.tools_for_test``.
    """
    from core.agent_harness.tools.tool_provider import DefaultToolProvider

    agent = fake_agent(dispatch_result=result)
    factory = MagicMock()

    def _build(**kwargs: Any) -> MagicMock:
        agent.tools_for_test = DefaultToolProvider(
            kwargs["session"],
            kwargs["console"],
            tool_action_logger=kwargs.get("logger"),
            observer_factory=kwargs.get("observer_factory"),
            subprocess_presenter_factory=kwargs.get("subprocess_presenter_factory"),
            slash_ports_factory=kwargs.get("slash_ports_factory"),
        )
        return agent

    factory.side_effect = _build
    factory.return_value = agent
    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(factory),
    )
    return factory


def test_turn_runner_resolves_action_tools_from_live_session(monkeypatch: Any) -> None:
    """Per-chat session integrations must drive the action tool list each turn.

    Precomputing tools at gateway boot (from an empty boot session) left the
    action agent with no integration-scoped tools, so ``run_turn`` fell through
    to the answer CLI agent on Telegram while the shell worked.
    """
    recorded: list[dict[str, Any] | None] = []

    def _fake_get_tools(
        _ctx: Any,
        *,
        resolved_integrations: dict[str, Any] | None = None,
    ) -> list[Any]:
        recorded.append(resolved_integrations)
        return [MagicMock(name="slack_send_message")]

    monkeypatch.setattr(
        "core.agent_harness.tools.tool_provider.get_action_tools_from_integrations_view",
        _fake_get_tools,
    )

    agent_cls = _patch_headless_agent(
        monkeypatch,
        TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
        ),
    )

    session = SessionCore(store=InMemorySessionStore())
    chat_integrations = {"slack": {"webhook_url": "https://hooks.example/test"}}
    session.resolved_integrations_cache = chat_integrations

    handler = TurnRunner(console=Console(force_terminal=False))
    handler("send slack update", session, MagicMock(), logging.getLogger("test.turn_runner"))

    tool_provider = agent_cls.return_value.tools_for_test
    tools = tool_provider.action_tools(confirm_fn=None, is_tty=False)
    assert len(tools) == 1
    assert recorded == [chat_integrations]


def _empty_turn_result(*, streamed: bool = False) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
            response_text="",
            response_streamed=streamed,
        ),
        assistant_response_text="",
    )


def _turn_result_with_text(text: str) -> TurnResult:
    """A handled turn whose primary response is ``text`` (not streamed, not answered)."""
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
            response_text=text,
        ),
        assistant_response_text=text,
    )


def test_turn_runner_continues_outer_loop_for_active_session_goal(
    monkeypatch: Any,
) -> None:
    """Gateway wraps chat in run_until_session_goal like the interactive shell."""
    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal

    agent_cls = _patch_headless_agent(monkeypatch, _empty_turn_result())
    calls: list[str] = []

    session = SessionCore(store=InMemorySessionStore())

    def _dispatch(message: str) -> TurnResult:
        # Each turn ticks the next checklist item the way ``session_goal_complete`` does.
        calls.append(message)
        stored = session.session_goal
        assert isinstance(stored, SessionGoal)
        attach_session_goal(session, stored.with_completed(stored.completed | {len(calls) - 1}))
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="step one" if len(calls) == 1 else "all done",
        )

    agent_cls.return_value.dispatch.side_effect = _dispatch
    attach_session_goal(
        session,
        SessionGoal(
            condition="two-step",
            max_outer_turns=3,
            checklist=("one", "two"),
        ),
    )
    sink = MagicMock()
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("go", session, sink, logging.getLogger("test"))

    assert len(calls) == 2
    sink.finalize.assert_called_once()
    finalized = sink.finalize.call_args.args[0]
    assert "all done" in finalized
    # Same mid-loop progress contract as the interactive shell.
    assert sink.set_tool_status.called
    status_texts = [call.args[0] for call in sink.set_tool_status.call_args_list]
    assert any("◎ /goal" in text and "turn " in text for text in status_texts)


def test_turn_runner_flushes_session_goal_for_next_resolve(
    monkeypatch: Any,
) -> None:
    """End-of-turn flush so the next inbound ``resolve`` sees goal mutations.

    Gateway rebuilds a fresh SessionCore from the transcript each message.
    Without flush, an in-memory pause/attach from this turn is lost and the
    outer loop can resume against the pre-pause snapshot.
    """
    from core.agent_harness.session import SessionCore, SessionManager
    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        SessionGoalStatus,
        attach_session_goal,
        session_goal_is_paused,
    )
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE

    _patch_headless_agent(monkeypatch, _empty_turn_result())
    store = InMemorySessionStore()
    session = SessionCore(store=store)
    store.open_session(session)
    store.append_turn(session, "chat", "seed")
    attach_session_goal(
        session,
        SessionGoal(
            condition="ship the fix",
            max_outer_turns=4,
            status=SessionGoalStatus.PAUSED,
            host_owned=True,
        ),
    )
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("side question", session, MagicMock(), logging.getLogger("test"))

    goal_records = [
        rec
        for rec in store.read(session.session_id)
        if rec.get("type") == "custom_message"
        and rec.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert goal_records
    payload = goal_records[-1]["content"]
    assert isinstance(payload, dict)
    goal_payload = payload.get("session_goal")
    assert isinstance(goal_payload, dict)
    assert goal_payload.get("status") == "paused"

    restored = SessionCore(store=InMemorySessionStore())
    SessionManager(store=InMemorySessionStore()).restore_context(
        restored,
        {
            "cli_agent_messages": [],
            "accumulated_context": {},
            "session_goal_state": payload,
            "history": [],
        },
    )
    assert session_goal_is_paused(restored)


def test_turn_runner_finalizes_fallback_on_empty_response(monkeypatch: Any) -> None:
    """An empty, non-answered turn still finalizes so the placeholder status can't hang."""
    _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = MagicMock()
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("/", SessionCore(store=InMemorySessionStore()), sink, logging.getLogger("test"))
    sink.finalize.assert_called_once_with("I didn't have anything to add for that.")


def test_turn_runner_skips_finalize_when_answer_was_streamed(monkeypatch: Any) -> None:
    """A streamed answer (llm_run set) already resolved the status; do not re-finalize."""
    result = _empty_turn_result(streamed=True)
    _patch_headless_agent(monkeypatch, result)
    sink = MagicMock()
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("hi", SessionCore(store=InMemorySessionStore()), sink, logging.getLogger("test"))
    sink.finalize.assert_not_called()


def test_turn_runner_skips_finalize_when_turn_cancelled(monkeypatch: Any) -> None:
    """Soft timeout / stop owns the sink; do not overwrite with empty finalize."""
    from infrastructure.turn_host.cancel_console import CancelConsole

    agent_cls = _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = MagicMock()

    def _dispatch(_message: str) -> TurnResult:
        cancel = sink.turn_cancel
        assert isinstance(cancel, threading.Event)
        cancel.set()
        return _empty_turn_result()

    agent_cls.return_value.dispatch.side_effect = _dispatch
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("hi", SessionCore(store=InMemorySessionStore()), sink, logging.getLogger("test"))
    sink.finalize.assert_not_called()
    console = agent_cls.return_value.bind_turn.call_args.args[0].console
    assert isinstance(console, CancelConsole)
    assert console.cancel_requested is True


def test_turn_runner_binds_cancel_console_each_turn(monkeypatch: Any) -> None:
    """Each turn rebinds a CancelConsole so timeout Events stay turn-scoped."""
    from infrastructure.turn_host.cancel_console import CancelConsole

    agent_cls = _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = MagicMock()
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("hi", SessionCore(store=InMemorySessionStore()), sink, logging.getLogger("test"))
    console = agent_cls.return_value.bind_turn.call_args.args[0].console
    assert isinstance(console, CancelConsole)
    assert console.cancel_requested is False
    assert isinstance(sink.turn_cancel, threading.Event)


def test_turn_runner_forwards_sink_tool_hooks_to_agent(monkeypatch: Any) -> None:
    """A sink carrying tool hooks (Slack's approval gate) rebinds them each turn."""
    agent_cls = _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = MagicMock()
    hooks = object()
    sink.tool_hooks = hooks
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("hi", SessionCore(store=InMemorySessionStore()), sink, logging.getLogger("test"))
    agent = agent_cls.return_value
    assert agent.bind_turn.call_args.args[0].tool_hooks is hooks


def test_turn_runner_tolerates_sinks_without_tool_hooks(monkeypatch: Any) -> None:
    """Sinks without tool_hooks (Telegram today) run unhooked — documented host gap."""

    class _BareSink:
        def finalize(self, answer: str) -> None:
            self.finalized = answer

    agent_cls = _patch_headless_agent(monkeypatch, _empty_turn_result())
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("hi", SessionCore(store=InMemorySessionStore()), _BareSink(), logging.getLogger("test"))
    agent = agent_cls.return_value
    assert agent.bind_turn.call_args.args[0].tool_hooks is None


def test_turn_runner_disables_unsupported_gateway_capabilities(monkeypatch: Any) -> None:
    _patch_headless_agent(monkeypatch, _empty_turn_result())
    session = SessionCore(store=InMemorySessionStore())
    handler = TurnRunner(console=Console(force_terminal=False))

    handler(
        "hello",
        session,
        RecordingTurnOutput(),
        logging.getLogger("test"),
    )

    assert session.available_capabilities["llm_provider"] == ()
    assert session.available_capabilities["task_cancel"] == ()


def test_turn_runner_preserves_supported_capabilities(monkeypatch: Any) -> None:
    _patch_headless_agent(monkeypatch, _empty_turn_result())
    session = SessionCore(store=InMemorySessionStore())
    session.available_capabilities.update(
        {
            "llm_provider": ("existing-provider",),
            "task_cancel": ("existing-cancel",),
            "shell_commands": ("shell",),
            "custom_gateway_capability": ("enabled",),
        }
    )

    handler = TurnRunner(console=Console(force_terminal=False))
    handler(
        "hello",
        session,
        RecordingTurnOutput(),
        logging.getLogger("test.gateway.capabilities"),
    )

    assert session.available_capabilities["llm_provider"] == ()
    assert session.available_capabilities["task_cancel"] == ()

    assert session.available_capabilities["shell_commands"] == ("shell",)
    assert session.available_capabilities["custom_gateway_capability"] == ("enabled",)


def test_turn_runner_capability_gating_is_stable_across_turns(monkeypatch: Any) -> None:
    _patch_headless_agent(monkeypatch, _empty_turn_result())
    session = SessionCore(store=InMemorySessionStore())
    session.available_capabilities["shell_commands"] = ("shell",)

    handler = TurnRunner(console=Console(force_terminal=False))
    logger = logging.getLogger("test.gateway.capabilities")

    handler("first turn", session, RecordingTurnOutput(), logger)
    handler("second turn", session, RecordingTurnOutput(), logger)

    assert session.available_capabilities["llm_provider"] == ()
    assert session.available_capabilities["task_cancel"] == ()
    assert session.available_capabilities["shell_commands"] == ("shell",)


def test_turn_runner_emits_gateway_turn_analytics(monkeypatch: Any) -> None:
    started: list[dict[str, object]] = []
    completed: list[dict[str, object]] = []

    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_started",
        lambda **kwargs: started.append(kwargs),
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_completed",
        lambda **kwargs: completed.append(kwargs),
    )
    _patch_headless_agent(monkeypatch, _empty_turn_result())

    from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context

    session = SessionCore(store=InMemorySessionStore())
    handler = TurnRunner(console=Console(force_terminal=False))
    with bound_usage_context(surface=UsageSurface.SLACK, user_id="U1"):
        handler("hi", session, MagicMock(), logging.getLogger("test"))

    assert started == [{"surface": UsageSurface.SLACK}]
    assert len(completed) == 1
    assert completed[0]["surface"] == UsageSurface.SLACK
    assert completed[0]["answered"] is False


def test_turn_runner_holds_the_session_lock_for_the_whole_turn(monkeypatch: Any) -> None:
    """The handler must take the pool's lock, not the unsynchronised primitive.

    ``session_agent`` holds the per-session lock across dispatch. Calling
    ``agent_for`` directly returns an unguarded agent, so an overlapping turn
    for the same session can rebind its session and sink mid-dispatch and route
    output to the wrong conversation.
    """
    # Arrange: record when the lock is held relative to the dispatch.
    _patch_headless_agent(monkeypatch, _empty_turn_result())
    events: list[str] = []
    handler = TurnRunner(console=Console(force_terminal=False))
    real_session_agent = handler._pool.session_agent

    @contextmanager
    def _tracking_session_agent(**kwargs: Any) -> Any:
        events.append("lock-acquired")
        with real_session_agent(**kwargs) as agent:
            yield agent
        events.append("lock-released")

    monkeypatch.setattr(handler._pool, "session_agent", _tracking_session_agent)

    # Act
    handler("hi", SessionCore(store=InMemorySessionStore()), MagicMock(), logging.getLogger("test"))

    # Assert: the turn ran inside the lock. Calling agent_for directly would
    # leave this empty, since session_agent would never be entered.
    assert events == ["lock-acquired", "lock-released"]


def test_turn_runner_forwards_agent_build_to_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    original_init = SessionAgentPool.__init__

    def _init(self: SessionAgentPool, **kwargs: Any) -> None:
        seen.append(kwargs.get("agent_build"))
        original_init(self, **kwargs)

    monkeypatch.setattr(SessionAgentPool, "__init__", _init)
    agent_build = AgentBuildConfig()
    TurnRunner(console=Console(force_terminal=False), agent_build=agent_build)
    assert seen == [agent_build]


def _handler_with_fake_agent(monkeypatch: Any, result: TurnResult) -> tuple[TurnRunner, MagicMock]:
    """A handler plus the fake agent it will build, so tests can read the binding."""
    factory = _patch_headless_agent(monkeypatch, result)
    return TurnRunner(console=Console(force_terminal=False)), factory.return_value


def _last_binding(agent: MagicMock) -> Any:
    """The ``TurnBinding`` the host applied for the most recent turn."""
    return agent.bind_turn.call_args.args[0]


def test_run_returns_the_turn_result_that_the_callback_discards(monkeypatch: Any) -> None:
    """``run`` hands the result back; ``__call__`` stays the four-argument callback.

    A caller with a live terminal (the interactive shell) needs ``final_intent``
    and the response text as values. Chat transports reply through the sink, so
    ``__call__`` must keep returning ``None`` for the four transports.
    """
    # Arrange
    expected = _empty_turn_result()
    handler, _agent = _handler_with_fake_agent(monkeypatch, expected)
    session = SessionCore(store=InMemorySessionStore())

    # Act
    returned = handler.run("hello", session, RecordingTurnOutput(), logging.getLogger("t"))
    discarded = handler("hello", session, RecordingTurnOutput(), logging.getLogger("t"))

    # Assert
    assert returned is expected
    assert discarded is None


def test_run_forwards_the_callers_terminal_context_to_the_turn(monkeypatch: Any) -> None:
    """A TTY caller's console, confirm callback and tty flag reach the binding.

    Without this the shell cannot route through the gateway: tool confirmation
    prompts and ``is_tty`` decide whether the agent may ask the user anything.
    """
    # Arrange
    handler, agent = _handler_with_fake_agent(monkeypatch, _empty_turn_result())
    caller_console = Console(force_terminal=False)

    def _confirm(_prompt: str) -> str:
        return "y"

    # Act
    handler.run(
        "hello",
        SessionCore(store=InMemorySessionStore()),
        RecordingTurnOutput(),
        logging.getLogger("t"),
        console=caller_console,
        confirm_fn=_confirm,
        is_tty=True,
    )

    # Assert
    binding = _last_binding(agent)
    assert binding.is_tty is True
    assert binding.confirm_fn is _confirm
    assert binding.console._output is caller_console  # noqa: SLF001 - CancelConsole wraps it


def test_run_without_caller_context_is_the_transport_path(monkeypatch: Any) -> None:
    """Omitting every keyword must reproduce what the four chat transports get.

    This is the no-regression guarantee: ``__call__`` forwards no keywords, so a
    default that drifted would change every transport's turn at once.
    """
    # Arrange
    handler, agent = _handler_with_fake_agent(monkeypatch, _empty_turn_result())

    # Act
    handler(
        "hello",
        SessionCore(store=InMemorySessionStore()),
        RecordingTurnOutput(),
        logging.getLogger("t"),
    )

    # Assert
    binding = _last_binding(agent)
    assert binding.is_tty is False
    assert binding.confirm_fn is None


def test_run_returns_none_and_says_at_capacity_when_the_gate_refuses(monkeypatch: Any) -> None:
    """At capacity the caller gets ``None``, not a result it would treat as a turn."""
    # Arrange
    from infrastructure.turn_host.concurrency import AT_CAPACITY_MESSAGE, TurnConcurrencyGate

    factory = _patch_headless_agent(monkeypatch, _empty_turn_result())
    gate = TurnConcurrencyGate(1)
    assert gate.try_acquire() is True  # the only slot is taken
    admission_check = MagicMock(return_value=True)
    handler = TurnRunner(
        console=Console(force_terminal=False),
        gate=gate,
        admission_check=admission_check,
    )
    sink = RecordingTurnOutput()

    # Act
    returned = handler.run(
        "hello", SessionCore(store=InMemorySessionStore()), sink, logging.getLogger("t")
    )

    # Assert
    assert returned is None
    assert sink.finalized == AT_CAPACITY_MESSAGE
    admission_check.assert_not_called()
    factory.assert_not_called()


def test_run_rejected_by_admission_never_starts_agent_work(monkeypatch: Any) -> None:
    """A billing denial owns its response and never constructs an agent."""
    factory = _patch_headless_agent(monkeypatch, _empty_turn_result())
    handler = TurnRunner(
        console=Console(force_terminal=False),
        admission_check=MagicMock(return_value=False),
    )

    returned = handler.run(
        "hello",
        SessionCore(store=InMemorySessionStore()),
        RecordingTurnOutput(),
        logging.getLogger("t"),
    )

    assert returned is None
    factory.assert_not_called()


def test_run_cancelled_before_admission_is_never_charged(monkeypatch: Any) -> None:
    """A turn stopped while queued must not reach a metering hook that debits."""
    factory = _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = RecordingTurnOutput()
    sink.turn_cancel = threading.Event()
    sink.turn_cancel.set()
    admission_check = MagicMock(return_value=True)

    returned = TurnRunner(
        console=Console(force_terminal=False),
        admission_check=admission_check,
    ).run(
        "hello",
        SessionCore(store=InMemorySessionStore()),
        sink,
        logging.getLogger("t"),
    )

    assert returned is None
    admission_check.assert_not_called()
    factory.assert_not_called()
    # The cancelling host owns the terminal message; the runner adds none.
    assert sink.finalized is None


def test_run_cancelled_during_successful_admission_still_starts_turn(
    monkeypatch: Any,
) -> None:
    """Once admission may debit, cancellation must not detach work from billing."""
    factory = _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = RecordingTurnOutput()
    sink.turn_cancel = threading.Event()

    def _admit_after_timeout() -> bool:
        sink.turn_cancel.set()
        return True

    handler = TurnRunner(
        console=Console(force_terminal=False),
        admission_check=_admit_after_timeout,
    )

    returned = handler.run(
        "hello",
        SessionCore(store=InMemorySessionStore()),
        sink,
        logging.getLogger("t"),
    )

    assert returned is not None
    factory.assert_called_once()


# ---------------------------------------------------------------------------
# Concurrency tests — ported up from test_session_agents.py to the door every
# host actually goes through.  The pool tests prove the lock works when the pool
# is called correctly; these prove TurnRunner calls it correctly.
# ---------------------------------------------------------------------------


def test_turn_runner_concurrent_sessions_do_not_bleed(monkeypatch: Any) -> None:
    """Two sessions through TurnRunner run concurrently; each sink gets only its own text.

    Port of ``test_session_agents.test_different_sessions_still_run_concurrently``
    up one layer.  Different sessions take different per-session locks, so with a
    raised capacity gate both turns must overlap inside dispatch — proved by a
    controllable Event neither dispatch can pass until the test releases it.
    """
    release = threading.Event()
    entered_alpha = threading.Event()
    entered_beta = threading.Event()

    def _dispatch(message: str) -> TurnResult:
        if message == "alpha":
            entered_alpha.set()
        else:
            entered_beta.set()
        release.wait(timeout=10)
        return _turn_result_with_text(f"reply-{message}")

    factory = _patch_headless_agent(monkeypatch, _turn_result_with_text(""))
    factory.return_value.dispatch.side_effect = _dispatch

    from infrastructure.turn_host.concurrency import TurnConcurrencyGate

    handler = TurnRunner(
        console=Console(force_terminal=False),
        gate=TurnConcurrencyGate(4),
    )
    session_a = SessionCore(store=InMemorySessionStore())
    session_b = SessionCore(store=InMemorySessionStore())
    sink_a = RecordingTurnOutput()
    sink_b = RecordingTurnOutput()
    logger = logging.getLogger("test.concurrent.sessions")

    results: dict[str, TurnResult | None] = {}
    errors: list[Exception] = []

    def _run(message: str, session: SessionCore, sink: Any) -> None:
        try:
            results[message] = handler.run(message, session, sink, logger)
        except Exception as exc:
            errors.append(exc)

    t_a = threading.Thread(target=_run, args=("alpha", session_a, sink_a))
    t_b = threading.Thread(target=_run, args=("beta", session_b, sink_b))

    # Act — both threads must reach dispatch before either can proceed.
    t_a.start()
    t_b.start()
    assert entered_alpha.wait(timeout=10), "session A never entered dispatch"
    assert entered_beta.wait(timeout=10), "session B never entered dispatch"
    release.set()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    # Assert — both returned, and neither sink received the other session's text.
    assert not errors, errors
    assert results.get("alpha") is not None
    assert results.get("beta") is not None
    assert sink_a.finalized == "reply-alpha"
    assert sink_b.finalized == "reply-beta"


def test_turn_runner_same_session_turns_do_not_interleave(monkeypatch: Any) -> None:
    """Two turns for one session through TurnRunner must serialize.

    Port of ``test_session_agents.test_same_session_turns_do_not_interleave``
    up one layer.  The per-session lock is held for the whole turn so a second
    turn cannot rebind the pooled ``BindableOutput`` while the first is still
    dispatching — without it, the first turn's remaining write lands on the
    second turn's sink.

    Serialization is pinned by the overlap / second-entered handshake.
    Sink isolation is pinned by writing through the pooled ``BindableOutput``
    (not ``TurnRunner``'s stack-local ``output.finalize``): that local finalize
    still hits the per-call sink even when the lock is gone, so it cannot
    detect the bleed #5493 named.
    """
    release = threading.Event()
    first_entered = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    overlapped = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    from infrastructure.turn_host.concurrency import TurnConcurrencyGate

    handler = TurnRunner(
        console=Console(force_terminal=False),
        gate=TurnConcurrencyGate(4),
    )
    session = SessionCore(store=InMemorySessionStore())
    sink_1 = RecordingTurnOutput()
    sink_2 = RecordingTurnOutput()
    logger = logging.getLogger("test.serialize.same_session")

    def _write_through_bound_output(message: str) -> None:
        # The real agent writes through this BindableOutput; rebinding it mid-
        # turn is the bleed path.  TurnRunner's stack-local finalize is not.
        bound = handler._pool._outputs[session.session_id]  # noqa: SLF001
        bound.print(message)

    def _dispatch(message: str) -> TurnResult:
        nonlocal call_count
        with call_lock:
            call_count += 1
            n = call_count
        if n == 1:
            first_entered.set()
            release.wait(timeout=10)
            _write_through_bound_output(message)
            return _turn_result_with_text(message)
        second_entered.set()
        if not release.is_set():
            overlapped.set()
        release.wait(timeout=10)
        _write_through_bound_output(message)
        return _turn_result_with_text(message)

    factory = _patch_headless_agent(monkeypatch, _turn_result_with_text(""))
    factory.return_value.dispatch.side_effect = _dispatch

    results: dict[str, TurnResult | None] = {}
    errors: list[Exception] = []

    def _run(message: str, sink: Any) -> None:
        if message == "beta":
            second_started.set()
        try:
            results[message] = handler.run(message, session, sink, logger)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_run, args=("alpha", sink_1))
    t2 = threading.Thread(target=_run, args=("beta", sink_2))

    # Act — the first dispatch holds the lock while blocked on ``release``.
    t1.start()
    assert first_entered.wait(timeout=5), "first turn never entered dispatch"
    t2.start()
    # Handshake: the second thread is running and heading for the lock.
    assert second_started.wait(timeout=5), "second thread never started"

    # If the lock works, the second is blocked and ``second_entered`` is
    # never set.  If the lock is bypassed, the second enters dispatch almost
    # immediately and sets ``second_entered`` (and ``overlapped``).
    try:
        assert not second_entered.wait(timeout=0.5), (
            "second dispatch entered while first still held the lock"
        )
        assert not overlapped.is_set(), "turns interleaved"
    finally:
        release.set()

    t1.join(timeout=5)
    t2.join(timeout=5)

    # Assert — no overlap, and each bound-output write hit its own sink.
    assert not errors, errors
    assert not overlapped.is_set(), "turns interleaved"
    assert second_entered.is_set(), "second dispatch never ran after first released"
    assert results.get("alpha") is not None
    assert results.get("beta") is not None
    assert sink_1.lines == ["alpha"], (
        "first turn's bound-output write must stay on sink_1 "
        f"(got {sink_1.lines!r}; sink_2={sink_2.lines!r})"
    )
    assert sink_2.lines == ["beta"], (
        "second turn's bound-output write must stay on sink_2 "
        f"(got {sink_2.lines!r}; sink_1={sink_1.lines!r})"
    )


@pytest.mark.parametrize(
    ("cancelled_msg", "survivor_msg"),
    [
        ("alpha", "beta"),
        ("beta", "alpha"),
    ],
    ids=["cancel-alpha-survive-beta", "cancel-beta-survive-alpha"],
)
def test_turn_runner_cancel_under_overlap_releases_slot_and_preserves_survivor(
    monkeypatch: Any,
    tmp_path: Any,
    cancelled_msg: str,
    survivor_msg: str,
) -> None:
    """Cancelling a turn while another session's turn runs is clean.

    The cancel path skips the normal dispatch return, so it is where a capacity
    slot is most likely to leak. Under overlap — two sessions, both turns
    provably in flight before the cancel — cancelling one turn must:

    * release the cancelled turn's capacity slot (the gate reflects only the
      survivor afterward),
    * leave the survivor's reply untouched, and
    * keep the cancelled session's JSONL file readable end to end (no torn line
      from a flush interrupted mid-append).

    Cancel is driven through :class:`ActiveTurnRegistry` — the path a
    transport's ``/stop`` takes — not by reaching into the runner's internals.
    The case is run twice with the roles reversed so it does not depend on
    whichever turn happened to start first.
    """
    import json

    from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
    from core.agent_harness.session.persistence.paths import session_path
    from gateway.core.middleware.active_turns import ActiveTurnRegistry
    from infrastructure.turn_host.concurrency import TurnConcurrencyGate

    # Session JSONL files land under OPENSRE_HOME_DIR; redirect to tmp_path so the
    # cancelled session's file is isolated and inspectable after the run.
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

    release = threading.Event()  # barrier the survivor blocks on until released
    entered_cancelled = threading.Event()
    entered_survivor = threading.Event()
    cancel_event = threading.Event()  # the cancelled turn's ``turn_cancel`` Event

    def _dispatch(message: str) -> TurnResult:
        if message == cancelled_msg:
            entered_cancelled.set()
            # Block until /stop sets the cancel event, then return. A real turn
            # checks cancel between LLM/tool iterations; the fake blocks on the
            # same Event the registry sets.
            cancel_event.wait(timeout=10)
            return _turn_result_with_text("")
        entered_survivor.set()
        release.wait(timeout=10)
        return _turn_result_with_text(f"reply-{message}")

    factory = _patch_headless_agent(monkeypatch, _turn_result_with_text(""))
    factory.return_value.dispatch.side_effect = _dispatch

    # Limit 2: both turns fill the gate, so a leaked slot leaves zero free and
    # the survivor cannot be mistaken for the leak.
    gate = TurnConcurrencyGate(2)
    handler = TurnRunner(console=Console(force_terminal=False), gate=gate)
    logger = logging.getLogger("test.cancel.overlap")

    # Cancelled session: pre-attach the cancel Event so the registry and the
    # runner share one signal, and seed a turn so flush writes an end-of-session
    # leaf (an empty session file is otherwise unlinked on flush).
    store_cancelled = JsonlSessionStore()
    session_cancelled = SessionCore(store=store_cancelled)
    store_cancelled.open_session(session_cancelled)
    store_cancelled.append_turn(session_cancelled, "chat", cancelled_msg)
    sink_cancelled = RecordingTurnOutput()
    sink_cancelled.turn_cancel = cancel_event

    # Survivor session: no cancel Event; runs to completion.
    store_survivor = JsonlSessionStore()
    session_survivor = SessionCore(store=store_survivor)
    store_survivor.open_session(session_survivor)
    store_survivor.append_turn(session_survivor, "chat", survivor_msg)
    sink_survivor = RecordingTurnOutput()

    registry = ActiveTurnRegistry()
    results: dict[str, TurnResult | None] = {}
    errors: list[Exception] = []

    def _run_cancelled() -> None:
        try:
            with registry.track(cancelled_msg, cancel_event):
                results[cancelled_msg] = handler.run(
                    cancelled_msg, session_cancelled, sink_cancelled, logger
                )
        except Exception as exc:
            errors.append(exc)

    def _run_survivor() -> None:
        try:
            results[survivor_msg] = handler.run(
                survivor_msg, session_survivor, sink_survivor, logger
            )
        except Exception as exc:
            errors.append(exc)

    t_cancelled = threading.Thread(target=_run_cancelled)
    t_survivor = threading.Thread(target=_run_survivor)
    t_cancelled.start()
    t_survivor.start()

    # Both turns must reach dispatch before the cancel — proving overlap.
    assert entered_cancelled.wait(timeout=10), "cancelled turn never entered dispatch"
    assert entered_survivor.wait(timeout=10), "survivor turn never entered dispatch"

    # Cancel via the path a transport /stop takes: request_stop sets the tracked
    # turn_cancel Event outside the turn lock.
    assert registry.request_stop(cancelled_msg) is True

    # The finally unblocks the survivor and joins both threads even when a slot
    # assertion fails, so a leaked-slot failure cannot leave a thread behind.
    probe_acquired = False
    try:
        # The cancelled turn returns from dispatch, flushes, and releases its slot.
        t_cancelled.join(timeout=10)
        assert not t_cancelled.is_alive(), "cancelled turn did not return after stop"

        # Slot assertion: the cancelled turn released its permit, so exactly one
        # slot is free (the survivor still holds the other). If the cancel path
        # leaked, the first try_acquire fails — the process would answer "at
        # capacity" forever.
        probe_acquired = gate.try_acquire()
        assert probe_acquired is True, "cancelled turn leaked its capacity slot"
        assert gate.try_acquire() is False, "survivor's slot was disturbed by the cancel"

        # Release the survivor; it must complete with its own reply, untouched.
        release.set()
        t_survivor.join(timeout=10)
        assert not t_survivor.is_alive(), "survivor turn did not complete"
        assert not errors, errors
        assert sink_survivor.finalized == f"reply-{survivor_msg}"
        assert results[survivor_msg] is not None

        # The cancelled session's JSONL must parse end to end — no torn line
        # from a flush interrupted mid-append (a real /stop leaves it readable).
        cancelled_path = session_path(session_cancelled.session_id)
        assert cancelled_path.exists(), f"cancelled session file missing: {cancelled_path}"
        for line in cancelled_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                json.loads(line)  # raises JSONDecodeError on a torn tail
    finally:
        if probe_acquired:
            gate.release()  # return the probe so the survivor's slot accounting stands
        release.set()
        t_cancelled.join(timeout=10)
        t_survivor.join(timeout=10)
