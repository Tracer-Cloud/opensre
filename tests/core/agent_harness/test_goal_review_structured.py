"""ReAct goal gates: host rejects stay on; same-LLM review is opt-in."""

from __future__ import annotations

from typing import Any

import pytest

from config.constants.llm import OPENSRE_REACT_GOAL_LLM_REVIEW_ENV
from core.agent.goals import GoalObservation
from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.turns.action_driver import _goal_review_user_request
from core.agent_harness.turns.goal_review import (
    build_gather_goal_reviewer,
    build_goal_reviewer,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.llm.types import AgentLLMResponse


class _ScriptedLLM:
    model_id = "test"

    def __init__(self, content: str) -> None:
        self.content = content
        self.invokes = 0

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        _ = tools
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (messages, system, tools)
        self.invokes += 1
        return AgentLLMResponse(content=self.content)


def _obs(*, text: str = "done", evidence: int = 1) -> GoalObservation:
    return GoalObservation(
        final_text=text,
        evidence_count=evidence,
        iteration=1,
        max_iterations=4,
    )


def test_goal_reviewer_rejects_while_task_plan_incomplete() -> None:
    """Do not idle with Plan · n/m and a mid-list ● — keep the turn going."""
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "check checkout latency",
        executed_tool_names=["update_plan", "call_mcp_tool"],
        plan_incomplete=lambda: True,
    )
    assert goal.verify is not None
    assert goal.verify(_obs()) is False
    assert llm.invokes == 0  # deterministic plan gate — no LLM spend
    assert goal.nudge is not None
    assert "unfinished steps" in goal.nudge(_obs())


def test_goal_reviewer_lets_an_active_goal_redirect_over_a_stale_plan() -> None:
    """A leftover plan must not pull a redirected /goal turn back into it."""
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "how are you doing?",
        executed_tool_names=["shell_run"],
        plan_incomplete=lambda: True,
    )
    assert goal.verify is not None
    assert goal.verify(_obs(text="Doing well.")) is True
    assert llm.invokes == 0


def test_goal_reviewer_lets_an_unrelated_turn_conclude_over_a_stale_plan() -> None:
    """A plan left from an earlier request does not pull this turn back into it."""
    # Arrange: the plan is unfinished, but this turn never touched it.
    llm = _ScriptedLLM('{"verdict": "NOT_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "how are you doing?",
        executed_tool_names=["call_mcp_tool"],
        plan_incomplete=lambda: True,
    )
    assert goal.verify is not None and goal.nudge is not None

    # Act
    accepted = goal.verify(_obs(text="Doing well."))
    nudge = goal.nudge(_obs(text="Doing well."))

    # Assert: no plan rejection, no plan nudge, no LLM spend.
    assert accepted is True
    assert "unfinished steps" not in nudge
    assert llm.invokes == 0


def test_goal_reviewer_ignores_plan_gate_when_complete() -> None:
    llm = _ScriptedLLM('{"verdict": "NOT_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "check checkout latency",
        executed_tool_names=["call_mcp_tool"],
        plan_incomplete=lambda: False,
    )
    assert goal.verify is not None
    assert goal.verify(_obs()) is True
    assert llm.invokes == 0


def test_goal_reviewer_opt_in_llm_still_accepts_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_REACT_GOAL_LLM_REVIEW_ENV, "1")
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "check checkout latency",
        executed_tool_names=["call_mcp_tool"],
        plan_incomplete=lambda: False,
    )
    assert goal.verify is not None
    assert goal.verify(_obs()) is True
    assert llm.invokes == 1


def test_task_plan_blocks_conclusion_helpers() -> None:
    from core.agent_harness.task_plan.plan import parse_task_plan
    from core.agent_harness.turns.goal_review import task_plan_blocks_conclusion

    incomplete, _ = parse_task_plan(
        {
            "plan": [
                {"step": "Discover", "status": "completed"},
                {"step": "Query", "status": "in_progress"},
                {"step": "Verify", "status": "pending"},
            ]
        }
    )
    done, _ = parse_task_plan(
        {
            "plan": [
                {"step": "Discover", "status": "completed"},
                {"step": "Verify", "status": "completed"},
            ]
        }
    )
    # Blocked steps are terminal: the gate must not force fake work to close them.
    settled, _ = parse_task_plan(
        {
            "plan": [
                {"step": "Discover", "status": "completed"},
                {"step": "Query", "status": "blocked"},
                {"step": "Verify", "status": "blocked"},
            ],
            "explanation": "Query blocked: the runtime has no metrics source.",
        }
    )
    assert incomplete is not None and done is not None and settled is not None
    assert task_plan_blocks_conclusion(task_plan=incomplete, plan_only=False) is True
    assert task_plan_blocks_conclusion(task_plan=incomplete, plan_only=True) is False
    assert task_plan_blocks_conclusion(task_plan=done, plan_only=False) is False
    assert task_plan_blocks_conclusion(task_plan=settled, plan_only=False) is False
    assert task_plan_blocks_conclusion(task_plan=None, plan_only=False) is False


def test_goal_reviewer_accepts_on_structured_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_REACT_GOAL_LLM_REVIEW_ENV, "1")
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is True


def test_goal_reviewer_fails_open_on_free_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prose without JSON must accept (fail open) — never force extra ReAct work."""
    monkeypatch.setenv(OPENSRE_REACT_GOAL_LLM_REVIEW_ENV, "1")
    llm = _ScriptedLLM("NOT_REACHED — keep going")
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is True


def test_goal_reviewer_skips_llm_by_default() -> None:
    llm = _ScriptedLLM('{"verdict": "NOT_REACHED"}')
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is True
    assert llm.invokes == 0


def test_gather_reviewer_rejects_discovery_only_without_llm() -> None:
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    calls = [("list_posthog_tools", {})]
    goal = build_gather_goal_reviewer(llm, "how many Windows users?", executed_tool_calls=calls)
    assert goal.verify is not None
    assert goal.verify(_obs()) is False
    assert llm.invokes == 0


def test_structured_answer_goal_review_recovers_latest_non_qa_user_request() -> None:
    original = "For facebook/react, return merged PR count, median time-to-merge, and star gain."
    prior_answers = format_ask_user_answers(
        (AskUserQuestion(label="Window", title="Which window?", options=("7d", "30d")),),
        ("7d",),
    )
    current_answers = format_ask_user_answers(
        (AskUserQuestion(label="Zone", title="Which timezone?", options=("UTC", "Local")),),
        ("UTC",),
    )
    snapshot = TurnSnapshot(
        text=current_answers,
        conversation_messages=(
            ("user", original),
            ("assistant", "Which window?"),
            ("user", prior_answers),
            ("assistant", "Which timezone?"),
        ),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
    )

    assert _goal_review_user_request(current_answers, snapshot) == original


def test_ordinary_turn_goal_review_uses_current_message() -> None:
    snapshot = TurnSnapshot(
        text="Run the same analysis for vercel/next.js",
        conversation_messages=(("user", "Analyze facebook/react"),),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
    )

    assert _goal_review_user_request(snapshot.text, snapshot) == snapshot.text
