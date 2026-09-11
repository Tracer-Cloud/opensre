"""Diagnostic evidence survives stop checks, display filtering, and persistence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core.agent.goals import should_accept_with_goal
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts
from core.agent_harness.turns.goal_review import build_goal_reviewer
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_trace import turn_trace_state
from infrastructure.observability.trace.spans import (
    bind_session_trace,
    get_session_trace_store,
    set_session_trace_store,
)
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.session.trace_store import JsonlSessionTraceStore
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)
from tests.core.agent_harness.test_action_turn_outcome import _painted_result


@pytest.fixture
def traced_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Session, Path]]:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_store.session_path",
        lambda sid: tmp_path / f"{sid}.jsonl",
    )
    session = Session(store=JsonlSessionStore())
    path = tmp_path / f"{session.session_id}.jsonl"
    path.write_text(json.dumps({"type": "session", "version": 2, "id": session.session_id}) + "\n")
    previous = get_session_trace_store()
    set_session_trace_store(JsonlSessionTraceStore(store=session.store))
    try:
        with bind_session_trace(session.session_id):
            yield session, path
    finally:
        set_session_trace_store(previous)


def _decisions(path: Path, name: str) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in path.read_text().splitlines()]
    return [
        row["attributes"]
        for row in records
        if row.get("span_kind") == "decision" and row.get("name") == name
    ]


def test_stop_trace_records_rejection_then_question_bypass_and_saved_answer(
    traced_session: tuple[Session, Path],
) -> None:
    session, path = traced_session
    # No tool runs before the write, so no step may already be completed.
    plan = [
        {"step": "Collect evidence", "status": "in_progress"},
        {"step": "Display report", "status": "pending"},
        {"step": "Offer next action", "status": "pending"},
    ]
    closing = "The report is ready. Would you like to schedule a check?"
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("update_plan", {"plan": plan}),
                no_tool_response("The report is ready."),
                no_tool_response(closing),
            ]
        )
    )

    def execute(text: str, **kwargs: Any) -> Any:
        return run_action_tool_turn(
            text, session, harness.console, llm_factory=harness.llm_factory, **kwargs
        )

    result = run_turn(
        "Analyze CI reliability",
        session,
        execute_actions=execute,
        accounting=DefaultTurnAccounting(session, "Analyze CI reliability"),
    )

    assert closing in result.assistant_response_text
    reviews = _decisions(path, "goal_review")
    assert [(row["accepted"], row["reason"]) for row in reviews] == [
        (False, "plan_incomplete"),
        (True, "closing_question"),
    ]
    assert all(row["task_plan"]["plan"] == plan for row in reviews)
    assert all(row["pending_user_choice"] is None for row in reviews)
    assert reviews[-1]["final_text"] == closing
    assert _decisions(path, "conclusion")[-1]["reason"] == "goal_check_accepted"
    assert _decisions(path, "response_composition")[-1]["display_text"] == closing
    assert _decisions(path, "response_displayed")[-1]["display_text"] == closing
    assert _decisions(path, "agent_finished")[-1]["final_text"] == closing
    persisted = _decisions(path, "assistant_persisted")[-1]
    assert closing in persisted["text"]
    records = [json.loads(line) for line in path.read_text().splitlines()]
    saved = next(row for row in records if row.get("id") == persisted["message_id"])
    assert saved["content"] == persisted["text"]


def test_trace_keeps_suppressed_closing_and_pending_question_with_secrets_redacted(
    traced_session: tuple[Session, Path],
) -> None:
    session, path = traced_session
    secret = "ghp_" + "a" * 36
    session.pending_user_choice = PendingUserChoice(
        title="Which repository?", options=("app", "api")
    )
    closing = (
        "36.4h red over 30 days, 333 of 1174 PR runs CI-caused. "
        + "Additional evidence. " * 100
        + secret
    )
    counts = _TurnCounts([], 1, 1, 1, 1, True)

    response, chunks, use_final = _compose_response(
        _painted_result(final_text=closing),
        session,
        counts,
    )

    assert response == "" and chunks == [] and use_final is False
    trace = _decisions(path, "response_composition")[-1]
    assert trace["final_text"] == closing.replace(secret, "[REDACTED:github_pat]")
    assert secret not in path.read_text()
    assert "[REDACTED:github_pat]" in trace["final_text"]
    assert trace["suppression_reason"] == "painted_figures_recap"
    assert trace["display_text"] == trace["response_text"] == ""
    assert trace["pending_user_choice"]["title"] == "Which repository?"


def test_trace_distinguishes_goal_rejection_from_iteration_ceiling_override(
    traced_session: tuple[Session, Path],
) -> None:
    session, path = traced_session
    goal = build_goal_reviewer(
        FakeActionLLM([]),
        "Finish the report",
        ["update_plan"],
        plan_incomplete=lambda: True,
        trace_context=lambda: turn_trace_state(session),
    )

    accepted, nudge = should_accept_with_goal(
        goal,
        final_text="Partial report.",
        evidence_count=1,
        iteration=2,
        max_iterations=3,
    )

    assert accepted and nudge is None
    assert _decisions(path, "goal_review")[-1]["accepted"] is False
    assert _decisions(path, "conclusion")[-1] == {
        "accepted": True,
        "reason": "iteration_ceiling",
        "iteration": 2,
    }
