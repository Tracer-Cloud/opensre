"""Component-level tests for modules on the shell ↔ gateway turn path."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import InMemorySessionStore
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
)
from core.agent_harness.spi.session_goal import pause_active_session_goal
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.host_cancel import HostCancelEvent, HostCancelReason
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from infrastructure.turn_host.turn_runner import TurnRunner
from surfaces.interactive_shell.session import Session
from tests.shared.default_headless_build_stub import default_headless_build_stub
from tests.shared.fake_agent import fake_agent


def test_gateway_turn_runner_delegates_to_agent_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = fake_agent()
    agent.dispatch.return_value = TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="gateway-ok",
        ),
        assistant_response_text="gateway-ok",
    )
    factory = MagicMock(return_value=agent)
    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(factory),
    )

    session = Session(store=InMemorySessionStore())
    sink = MagicMock()
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("hello gateway", session, sink, logging.getLogger("test.gateway.module"))

    # The message is dispatched per-turn; session-stable ports are wired once,
    # with a live sink proxy rebound to the transport sink each turn.
    from infrastructure.turn_host.bindable_output import BindableOutput

    agent.dispatch.assert_called_once()
    assert agent.dispatch.call_args.args == ("hello gateway",)
    ctor = factory.call_args
    assert ctor.kwargs["session"] is session
    assert isinstance(ctor.kwargs["output"], BindableOutput)
    assert ctor.kwargs["output"].bound is sink
    assert "gather" not in ctor.kwargs
    assert ctor.kwargs["surface"] == "gateway"
    tool_provider = DefaultToolProvider(
        ctor.kwargs["session"],
        ctor.kwargs["console"],
        tool_action_logger=ctor.kwargs["logger"],
        observer_factory=ctor.kwargs.get("observer_factory"),
        subprocess_presenter_factory=ctor.kwargs.get("subprocess_presenter_factory"),
        slash_ports_factory=ctor.kwargs.get("slash_ports_factory"),
    )
    assert tool_provider._precomputed_action_tools is None
    sink.finalize.assert_called_once_with("gateway-ok")


def test_gateway_turn_runner_does_not_finalize_answered_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = fake_agent()
    agent.dispatch.return_value = TurnResult(
        final_intent="cli_agent_fallback",
        action_result=ToolCallingTurnResult(
            0,
            0,
            0,
            False,
            False,
            response_streamed=True,
            hit_iteration_cap=True,
        ),
        assistant_response_text="streamed answer",
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(MagicMock(return_value=agent)),
    )

    session = Session(store=InMemorySessionStore())
    sink = MagicMock()
    handler = TurnRunner(console=Console(force_terminal=False))
    handler("why", session, sink, logging.getLogger("test.gateway.module.answer"))

    sink.finalize.assert_not_called()


def test_prestart_goal_pause_does_not_consume_admission_credit() -> None:
    session = Session(store=InMemorySessionStore())
    attach_session_goal(session, SessionGoal(condition="keep going", max_outer_turns=4))
    pause_active_session_goal(session)
    cancel = HostCancelEvent()
    cancel.request(HostCancelReason.GOAL_PAUSE)
    sink = MagicMock(turn_cancel=cancel)
    admission = MagicMock(return_value=True)

    result = TurnRunner(
        console=Console(force_terminal=False),
        admission_check=admission,
    ).run(
        "queued work",
        session,
        sink,
        logging.getLogger("test.gateway.prestart-pause"),
    )

    assert result is None
    admission.assert_not_called()
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.PAUSED


def test_run_turn_returns_agent_conclusion_directly() -> None:
    action = ToolCallingTurnResult(
        0,
        0,
        0,
        False,
        False,
        response_text="answered",
    )

    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return action

    class _Accounting:
        def record_action_result(self, _result: ToolCallingTurnResult) -> None:
            return None

        def finalize(self, result: TurnResult) -> TurnResult:
            return result

    session = Session(store=InMemorySessionStore())
    result = run_turn(
        "question?",
        session,
        execute_actions=execute_actions,
        accounting=_Accounting(),
    )

    assert result.final_intent == "agent_completed"
    assert result.answered is True


def test_run_turn_surfaces_iteration_cap_as_incomplete() -> None:
    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            1,
            1,
            1,
            False,
            True,
            response_text="intermediate tool output",
            hit_iteration_cap=True,
        )

    class _Accounting:
        def record_action_result(self, _result: ToolCallingTurnResult) -> None:
            return None

        def finalize(self, result: TurnResult) -> TurnResult:
            return result

    result = run_turn(
        "finish the task",
        Session(store=InMemorySessionStore()),
        execute_actions=execute_actions,
        accounting=_Accounting(),
    )

    assert result.final_intent == "agent_incomplete"
    assert "iteration limit reached" in result.primary_response_text


def test_run_turn_does_not_duplicate_a_streamed_safety_handoff() -> None:
    handoff = "Partial results are preserved; the repeated query is still blocked."

    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            1,
            1,
            0,
            False,
            True,
            response_text=handoff,
            response_streamed=True,
            hit_iteration_cap=True,
        )

    class _Accounting:
        def record_action_result(self, _result: ToolCallingTurnResult) -> None:
            return None

        def finalize(self, result: TurnResult) -> TurnResult:
            return result

    result = run_turn(
        "finish the task",
        Session(store=InMemorySessionStore()),
        execute_actions=execute_actions,
        accounting=_Accounting(),
    )

    assert result.final_intent == "agent_incomplete"
    assert result.primary_response_text == handoff


def test_run_turn_builds_turn_plan_for_action_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_turn resolves once and hands the action path a turn_plan carrying them."""
    resolved = {"github": {"configured": True}}
    monkeypatch.setattr(
        "core.agent_harness.turns.turn_plan.resolve_and_cache_integrations",
        lambda _session: resolved,
    )
    captured: list[Any] = []

    def execute_actions(
        _text: str, *, turn_plan: Any = None, **_kwargs: object
    ) -> ToolCallingTurnResult:
        captured.append(turn_plan)
        return ToolCallingTurnResult(0, 0, 0, False, False)

    class _Accounting:
        def record_action_result(self, _result: ToolCallingTurnResult) -> None:
            return None

        def finalize(self, result: TurnResult) -> TurnResult:
            return result

    session = Session(store=InMemorySessionStore())
    run_turn(
        "check facebook/react",
        session,
        execute_actions=execute_actions,
        accounting=_Accounting(),
    )

    assert captured, "execute_actions was never called"
    assert captured[0].resolved_integrations == {
        "github": {
            "configured": True,
            "owner": "facebook",
            "repo": "react",
        }
    }
    assert resolved == {"github": {"configured": True}}


def test_action_tools_uses_passed_resolved_integrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The turn's resolved dict is what tools are built from — no second resolve."""
    captured: list[dict[str, Any]] = []

    def _fake_build(_ctx: Any, *, resolved_integrations: dict[str, Any]) -> list[Any]:
        captured.append(resolved_integrations)
        return []

    monkeypatch.setattr(
        "core.agent_harness.tools.tool_provider.get_action_tools_from_integrations_view",
        _fake_build,
    )
    provider = DefaultToolProvider(
        Session(store=InMemorySessionStore()), Console(force_terminal=False)
    )
    turn_resolved = {"github": {"configured": True}}

    provider.action_tools(confirm_fn=None, is_tty=False, resolved_integrations=turn_resolved)

    assert captured == [turn_resolved]


def test_action_tools_falls_back_to_session_resolve_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting the turn's dict keeps the prior behavior: resolve from the session."""
    captured: list[dict[str, Any]] = []
    session_resolved = {"slack": {"configured": True}}

    def _fake_build(_ctx: Any, *, resolved_integrations: dict[str, Any]) -> list[Any]:
        captured.append(resolved_integrations)
        return []

    monkeypatch.setattr(
        "core.agent_harness.tools.tool_provider.get_action_tools_from_integrations_view",
        _fake_build,
    )
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_and_cache_integrations",
        lambda _session: dict(session_resolved),
    )
    provider = DefaultToolProvider(
        Session(store=InMemorySessionStore()), Console(force_terminal=False)
    )

    provider.action_tools(confirm_fn=None, is_tty=False)

    assert captured == [session_resolved]
