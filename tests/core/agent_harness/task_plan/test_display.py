"""Tests for task-plan terminal display helpers."""

from __future__ import annotations

from core.agent_harness.task_plan.display import (
    ensure_active_step,
    is_plan_diagnosis_prose,
    promote_first_pending_step,
)
from core.agent_harness.task_plan.plan import PlanStepStatus, parse_task_plan


def test_is_plan_diagnosis_prose_detects_established_facts_block() -> None:
    text = (
        "**Established facts**\n"
        "- Onset: gradual\n\n"
        "**Hypotheses**\n"
        "| Priority | Hypothesis | Why |\n"
        "| --- | --- | --- |"
    )
    assert is_plan_diagnosis_prose(text) is True


def test_is_plan_diagnosis_prose_detects_facts_hypothesis_table() -> None:
    text = (
        "Facts: gradual degradation on /api/orders.\n\n"
        "Hypothesis (ranked):\n"
        "| rank | hypothesis | why | next check |\n"
        "| --- | --- | --- | --- |"
    )
    assert is_plan_diagnosis_prose(text) is True


def test_is_plan_diagnosis_prose_ignores_short_explanation() -> None:
    assert is_plan_diagnosis_prose("Revised after deploy window narrowed.") is False


def test_is_plan_diagnosis_prose_ignores_empty() -> None:
    assert is_plan_diagnosis_prose("") is False
    assert is_plan_diagnosis_prose("   ") is False


def test_is_plan_diagnosis_prose_detects_facts_and_leading_hypothesis() -> None:
    text = "Facts: p99 only.\nLeading hypothesis: a queue knee after deploy."
    assert is_plan_diagnosis_prose(text) is True


def test_promote_first_pending_step_is_noop_when_work_already_started() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "First", "status": "in_progress"},
                {"step": "Verify", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    assert promote_first_pending_step(plan) is plan


def test_promote_first_pending_step_preserves_explanation() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "First", "status": "pending"},
                {"step": "Verify", "status": "pending"},
            ],
            "explanation": "### Facts\n- gradual",
        }
    )
    assert error is None and plan is not None
    promoted = promote_first_pending_step(plan)
    assert promoted.explanation == plan.explanation
    assert "gradual" in promoted.explanation
    assert promoted.steps[0].status is PlanStepStatus.IN_PROGRESS


def test_ensure_active_step_never_promotes_a_blocked_step() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Confirm source", "status": "completed"},
                {"step": "Query latency", "status": "blocked"},
                {"step": "Verify", "status": "blocked"},
            ],
            "explanation": "Query blocked: no metrics source is connected.",
        }
    )
    assert error is None and plan is not None
    assert ensure_active_step(plan) is plan
    assert promote_first_pending_step(plan) is plan


def test_ensure_active_step_promotes_after_completed_gap() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Confirm source", "status": "completed"},
                {"step": "Query latency", "status": "pending"},
                {"step": "Verify", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    # promote_first_pending_step stays all-pending-only; ensure covers the gap.
    assert promote_first_pending_step(plan) is plan
    fixed = ensure_active_step(plan)
    assert fixed.steps[1].status is PlanStepStatus.IN_PROGRESS
    assert fixed.current_index == 2
