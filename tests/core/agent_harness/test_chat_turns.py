"""Characterization tests for ``AgentSession.chat``."""

from __future__ import annotations

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.runtime import TurnBinding
from core.agent_harness.turns.headless_adapters import NullToolProvider
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _empty_action() -> ToolCallingTurnResult:
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=False,
        handled=False,
    )


def test_chat_requires_attached_agent() -> None:
    harness = AgentSession(SessionConfig(load_env=False, open_store=False))
    try:
        harness.chat("hi")
    except RuntimeError as exc:
        assert "attach_agent" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_chat_reuses_attached_agent() -> None:
    calls: list[str] = []

    class _Agent:
        def dispatch(self, message: str) -> TurnResult:
            calls.append(message)
            return TurnResult(
                final_intent="cli_agent_handled",
                action_result=_empty_action(),
                assistant_response_text=f"ok:{message}",
            )

    harness = AgentSession(SessionConfig(load_env=False))
    harness.attach_agent(_Agent())  # type: ignore[arg-type]
    first = harness.chat("one")
    second = harness.chat("two")

    assert calls == ["one", "two"]
    assert first.assistant_response_text == "ok:one"
    assert second.assistant_response_text == "ok:two"


def test_headless_bind_turn_swaps_output() -> None:
    from core.agent_harness.turns.headless_adapters import BufferOutputSink

    first = BufferOutputSink()
    second = BufferOutputSink()
    agent = InMemoryHeadlessBuild(output=first).agent(tools=NullToolProvider())
    before_runner = agent._action_runner  # noqa: SLF001
    agent.bind_turn(TurnBinding(output=second))
    assert agent._output is second  # noqa: SLF001
    assert agent._action_runner is not before_runner  # noqa: SLF001
    assert agent._action_runner.output is second  # noqa: SLF001
