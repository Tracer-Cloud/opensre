"""Characterization: ``AgentSession.run_headless_turn`` is the scheduled-runner stack.

Scheduled digests must not reassemble session + BufferOutputSink +
``DefaultHeadlessBuild`` locally. This pins the shared API wiring.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.runtime import TurnBinding
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from tests.shared.default_headless_build_stub import default_headless_build_stub


def _answered(text: str) -> TurnResult:
    return TurnResult(
        final_intent="chat",
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


def test_run_headless_turn_wires_startup_sink_and_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(name="session")
    prompts = MagicMock(name="prompts")
    sink = MagicMock(name="sink")
    agent = MagicMock(name="agent")
    expected = _answered("digest body")
    agent.dispatch.return_value = expected

    built: dict[str, Any] = {}

    def _fake_build(**kwargs: Any) -> Any:
        built.update(kwargs)
        return agent

    class _Session(AgentSession):
        def startup(self) -> Any:  # type: ignore[override]
            from core.agent_harness.harness import SessionStartupResult

            return SessionStartupResult(session=session, prompts=prompts)

    monkeypatch.setattr(
        "core.agent_harness.turns.headless_build.DefaultHeadlessBuild",
        default_headless_build_stub(_fake_build),
    )
    monkeypatch.setattr(
        "core.agent_harness.turns.headless_adapters.BufferOutputSink",
        lambda: sink,
    )

    result = _Session.run_headless_turn(
        "summarize sentry",
        config=SessionConfig(load_env=False, open_store=False),
        logger=MagicMock(),
        is_tty=False,
    )

    assert result is expected
    assert built["session"] is session
    assert built["output"] is sink
    assert built["prompts"] is prompts
    assert "message" not in built  # accounting is built at dispatch, not at construction
    agent.bind_turn.assert_any_call(TurnBinding(is_tty=False))
    agent.dispatch.assert_called_once_with("summarize sentry")


def test_run_headless_turn_prepare_session_runs_before_agent_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(name="session")
    order: list[str] = []

    def _prepare(s: Any) -> None:
        assert s is session
        order.append("prepare")

    def _fake_build(**_kwargs: Any) -> Any:
        order.append("build")
        agent = MagicMock()
        agent.dispatch.return_value = _answered("ok")
        return agent

    class _Session(AgentSession):
        def startup(self) -> Any:  # type: ignore[override]
            from core.agent_harness.harness import SessionStartupResult

            return SessionStartupResult(session=session, prompts=None)

    monkeypatch.setattr(
        "core.agent_harness.turns.headless_build.DefaultHeadlessBuild",
        default_headless_build_stub(_fake_build),
    )
    monkeypatch.setattr(
        "core.agent_harness.turns.headless_adapters.BufferOutputSink",
        MagicMock,
    )

    _Session.run_headless_turn(
        "hi",
        config=SessionConfig(load_env=False, open_store=False),
        prepare_session=_prepare,
    )

    assert order == ["prepare", "build"]


def test_morning_digest_runner_uses_run_headless_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One scheduled runner must go through the shared API (not a private stack)."""
    from integrations.sentry import morning_digest_runner as runner

    calls: list[dict[str, Any]] = []

    def _fake_run(message: str, **kwargs: Any) -> TurnResult:
        calls.append({"message": message, **kwargs})
        return _answered("ok")

    monkeypatch.setattr(
        runner,
        "configured_integration_services",
        lambda: {"sentry"},
    )
    monkeypatch.setattr(AgentSession, "run_headless_turn", _fake_run)

    report = runner.run_sentry_morning_digest({"project_slug": "api"})

    assert report == "ok"
    assert len(calls) == 1
    assert "Project scope is fixed to 'api'" in calls[0]["message"]
    assert callable(calls[0].get("prepare_session"))
