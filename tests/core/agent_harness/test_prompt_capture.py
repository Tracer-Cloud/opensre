"""Shared prompt capture must cover headless turns without crossing session boundaries."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from config.principal import Actor, Principal, StorageScope
from config.prompt_log import PromptLogConfig
from config.scope_context import bound_storage_scope
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import TurnBinding
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.turns.headless_adapters import NullToolProvider
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from infrastructure.analytics.prompt_log import recorder as prompt_log
from infrastructure.analytics.repl_context import get_cli_session_id, get_prompt_turn_id


@pytest.fixture
def captured(monkeypatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(prompt_log, "capture_ai_generation", events.append)
    monkeypatch.setattr("config.prompt_log.read_prompt_log_settings", lambda: {})
    return events


def _session() -> SessionCore:
    session = SessionCore(store=InMemorySessionStore())
    session.resolved_integrations_cache = {}
    session.store.open_session(session)
    return session


def test_real_run_attaches_provider_evidence_and_preserves_missing_usage(
    captured, monkeypatch
) -> None:
    from core.llm.types import AgentLLMResponse
    from tests.core.agent.orchestration.action_execution_test_harness import FakeActionLLM

    client = FakeActionLLM([AgentLLMResponse(content="Recorded answer", output_tokens=0)])
    monkeypatch.setattr(client, "_model", "test-model", raising=False)
    monkeypatch.setattr(client, "_provider_label", "test-provider", raising=False)
    session = _session()
    agent = InMemoryHeadlessBuild(session=session).agent(
        tools=NullToolProvider(),
        llm_factory=lambda: client,
    )
    agent.handle("Hello", TurnBinding(session=session))
    assert len(captured) == 1
    event = captured[0]
    assert event["llm_attempted"] is True
    assert event["$ai_model"] == "test-model"
    assert event["$ai_provider"] == "test-provider"
    assert event["turn_outcome"] == "completed"
    assert event["response_source"] == "captured"
    assert "$ai_input_tokens" not in event
    assert event["$ai_output_tokens"] == 0
    assert event["token_usage_status"] == "partial"


def _reply(text: str, **_kwargs: Any) -> ToolCallingTurnResult:
    return ToolCallingTurnResult(1, 1, 1, False, True, response_text=text)


def test_failed_static_dispatch_never_claims_a_provider_attempt(captured, monkeypatch) -> None:
    def _failed_run(_agent, _messages):
        raise RuntimeError("Tool dispatch failed")

    def _unexpected_provider():
        raise AssertionError("A literal shell command must not select a provider")

    monkeypatch.setattr("core.agent.Agent.run", _failed_run)
    session = _session()
    agent = InMemoryHeadlessBuild(session=session).agent(
        tools=NullToolProvider(), llm_factory=_unexpected_provider
    )

    agent.handle("!echo hello", TurnBinding(session=session))

    assert len(captured) == 1
    event = captured[0]
    assert event["llm_attempted"] is False
    assert event["$ai_model"] == "no_conversational_agent"
    assert event["$ai_provider"] == "no_conversational_agent"
    assert event["$ai_is_error"] is True
    assert event["turn_outcome"] == "error"
    assert event["token_usage_status"] == "unavailable"


def _turn(session: SessionCore, text: str, execute=_reply, surface="gateway"):
    return run_turn(
        text,
        session,
        execute_actions=execute,
        accounting=DefaultTurnAccounting(session, text),
        surface=surface,
    )


@pytest.mark.parametrize("surface", ["gateway", "headless_cli", "scheduled", "embedded"])
def test_each_host_records_prompt_reply_and_session_once(captured, surface) -> None:
    session = _session()
    result = _turn(session, "Original question", surface=surface)
    assert result.primary_response_text == "Original question"
    assert len(captured) == 1
    event = captured[0]
    assert event["$ai_input"][0]["content"] == "Original question"
    assert event["$ai_output_choices"][0]["content"] == "Original question"
    assert event["$ai_span_name"] == f"surfaces.{surface}.agent"
    assert event["cli_session_id"] == session.session_id
    messages = [row for row in session.store.read(session.session_id) if row["type"] == "message"]
    assert [row["role"] for row in messages] == ["user", "assistant"]
    assert {row["metadata"]["turn_id"] for row in messages} == {event["cli_turn_id"]}


def test_goal_continuation_records_each_prompt_with_a_new_id(captured) -> None:
    session = _session()
    agent = InMemoryHeadlessBuild(session=session).agent(tools=NullToolProvider())
    agent.bind_stages(execute_actions=_reply)
    run = agent.run_goal(
        "First request",
        TurnBinding(session=session),
        goal=SessionGoal(condition="Complete the repair", max_outer_turns=2),
        evaluate=lambda *_args, **_kwargs: "active",
    )
    assert run.turn_count == 2
    assert len(captured) == 2
    assert len({event["cli_turn_id"] for event in captured}) == 2
    assert captured[0]["$ai_input"] != captured[1]["$ai_input"]
    assert {event["cli_session_id"] for event in captured} == {session.session_id}


def test_nested_disabled_turn_cannot_reuse_parent_correlation(captured, monkeypatch) -> None:
    outer, inner = _session(), _session()
    seen: list[str | None] = []

    def child(text: str, **kwargs: Any) -> ToolCallingTurnResult:
        assert get_prompt_turn_id() is None
        assert prompt_log.PromptRecorder.current() is None
        return _reply(text)

    def parent(text: str, **kwargs: Any) -> ToolCallingTurnResult:
        seen.append(get_prompt_turn_id())
        with monkeypatch.context() as patch:
            patch.setenv("OPENSRE_PROMPT_LOG_DISABLED", "1")
            _turn(inner, "Private child request", child)
        assert get_prompt_turn_id() == seen[0]
        assert get_cli_session_id() == outer.session_id
        return _reply(text)

    _turn(outer, "Parent request", parent)
    assert len(captured) == 1
    assert captured[0]["cli_turn_id"] == seen[0]
    assert get_prompt_turn_id() is None
    assert prompt_log.PromptRecorder.current() is None


def test_concurrent_sessions_keep_their_own_prompt_ids(captured) -> None:
    barrier = threading.Barrier(2, timeout=10)
    sessions = [_session(), _session()]

    def work(session: SessionCore) -> None:
        def execute(text: str, **kwargs: Any) -> ToolCallingTurnResult:
            before = get_prompt_turn_id()
            barrier.wait()
            assert before == get_prompt_turn_id()
            assert get_cli_session_id() == session.session_id
            return _reply(text)

        _turn(session, session.session_id, execute)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(work, sessions))
    assert len(captured) == 2
    assert len({event["cli_turn_id"] for event in captured}) == 2
    for event in captured:
        assert event["$ai_input"][0]["content"] == event["cli_session_id"]


def test_disabled_capture_writes_no_prompt_log_or_analytics(captured, monkeypatch) -> None:
    monkeypatch.setenv("OPENSRE_PROMPT_LOG_DISABLED", "1")
    log_path = PromptLogConfig.load().log_path
    _turn(_session(), "Sensitive request")
    assert captured == []
    assert not log_path.exists()


def test_default_prompt_log_path_is_scoped_to_org_and_actor(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_PROMPT_LOG_PATH", raising=False)
    monkeypatch.setattr("config.prompt_log.read_prompt_log_settings", lambda: {})
    paths = []
    for actor in ("alice", "bob"):
        with bound_storage_scope(StorageScope(Principal.org("test-org"), Actor(actor))):
            paths.append(PromptLogConfig.load().log_path)
    assert paths[0] != paths[1]
    assert paths[0].parts[-3:] == ("users", "alice", "prompt_log.jsonl")
    assert paths[1].parts[-3:] == ("users", "bob", "prompt_log.jsonl")
