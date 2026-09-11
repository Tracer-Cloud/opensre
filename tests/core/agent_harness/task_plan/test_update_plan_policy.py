"""Tests for update_plan host policy."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    format_ask_user_answers,
)
from core.agent_harness.task_plan.evidence import (
    mark_plan_written,
    plan_evidence_available,
    record_plan_evidence,
)
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan, parse_task_plan
from core.agent_harness.task_plan.update_plan_policy import (
    apply_update_plan_host_policy,
    demote_unevidenced_completions,
)
from core.agent_harness.tools.tool_context import ActionToolScope
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.update_plan import execute_update_plan_tool


def _ask_user_turn_text() -> str:
    return format_ask_user_answers(
        (
            AskUserQuestion(label="Onset", title="When did it start?", options=("Gradual",)),
            AskUserQuestion(label="Signal", title="Strongest signal?", options=("CPU",)),
        ),
        ("Gradual", "CPU"),
    )


_PLAN: list[dict[str, Any]] = [
    {"step": "Pinpoint onset on p99", "status": "pending"},
    {"step": "Confirm checkout returns 2xx", "status": "pending"},
]


def test_ask_user_turn_strips_plan_only_by_default() -> None:
    session = Session()
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=True,
        turn_user_message=_ask_user_turn_text(),
        session=session,
    )
    assert plan_only is False
    assert normalized.steps[0].status is PlanStepStatus.IN_PROGRESS


def test_ask_user_turn_honors_armed_plan_only_latch() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=True,
        turn_user_message=_ask_user_turn_text(),
        session=session,
    )
    assert plan_only is True
    assert normalized.all_pending is True
    # Set-only: the policy does not consume the latch — only the gate lifts it.
    assert session.plan_only_until_authorized is True


def test_ask_user_turn_model_cannot_drop_user_plan_only() -> None:
    """A plan-only Ask User hand-off stays gated even if the model sends false."""
    session = Session()
    session.plan_only_until_authorized = True
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=False,
        turn_user_message=_ask_user_turn_text(),
        session=session,
    )
    assert plan_only is True
    assert normalized.all_pending is True


def test_ask_user_turn_existing_latch_survives_model_false() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=False,
        turn_user_message=_ask_user_turn_text(),
        session=session,
    )
    assert plan_only is True
    assert normalized.all_pending is True


def test_update_plan_tool_end_to_end_after_ask_user() -> None:
    session = Session()
    ctx = ActionToolScope(
        session=session,
        console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
        turn_user_message=_ask_user_turn_text(),
    )
    result = execute_update_plan_tool({"plan": _PLAN, "plan_only": True}, ctx)
    assert result["ok"] is True
    assert session.plan_only_until_authorized is False
    assert session.task_plan is not None
    assert session.task_plan.steps[0].status is PlanStepStatus.IN_PROGRESS


def test_normal_turn_honors_requested_plan_only() -> None:
    session = Session()
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=True,
        turn_user_message="plan this, do not run yet",
        session=session,
    )
    assert plan_only is True
    assert normalized.all_pending is True


def test_normal_turn_promotes_gap_after_completed_step() -> None:
    session = Session()
    plan, _error = parse_task_plan(
        {
            "plan": [
                {"step": "Confirm telemetry source", "status": "completed"},
                {"step": "Query latency", "status": "pending"},
                {"step": "Verify baseline", "status": "pending"},
            ]
        }
    )
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=False,
        turn_user_message="run the plan",
        session=session,
    )
    assert plan_only is False
    assert normalized.steps[1].status is PlanStepStatus.IN_PROGRESS
    assert normalized.current_index == 2


def test_normal_turn_promotes_all_pending_when_execution_authorized() -> None:
    session = Session()
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    normalized, plan_only = apply_update_plan_host_policy(
        plan,
        plan_only_requested=False,
        turn_user_message="make a plan and run it",
        session=session,
    )
    assert plan_only is False
    assert normalized.steps[0].status is PlanStepStatus.IN_PROGRESS


def _plan(*statuses: str) -> TaskPlan:
    plan, error = parse_task_plan(
        {"plan": [{"step": f"Step {i}", "status": s} for i, s in enumerate(statuses, 1)]}
    )
    assert error is None and plan is not None
    return plan


def _statuses(plan: TaskPlan) -> tuple[str, ...]:
    return tuple(str(item.status) for item in plan.steps)


def test_completions_without_tool_evidence_are_reset_to_pending() -> None:
    """The demo run: two ``update_plan`` writes ticked steps whose tools had not run yet.

    Write 1 (no prior plan, no tool yet) marked step 2 completed. Write 2 came after
    one tool returned and ticked steps 1-3, though only step 3 had been in_progress:
    a pending step cannot have been worked, so steps 1 and 2 are reset; step 3 keeps
    its tick because work returned while it was the active step.
    """
    first, demoted = demote_unevidenced_completions(
        _plan("pending", "completed", "in_progress", "pending"), prior=None, evidence=False
    )
    assert demoted == ("Step 2",)
    assert _statuses(first) == ("pending", "pending", "in_progress", "pending")

    second, demoted = demote_unevidenced_completions(
        _plan("completed", "completed", "completed", "in_progress"), prior=first, evidence=True
    )
    assert demoted == ("Step 1", "Step 2")
    assert _statuses(second) == ("pending", "pending", "completed", "in_progress")


def test_plan_written_after_the_work_keeps_its_completed_steps() -> None:
    """A retroactive plan (tools ran, then update_plan) is not a false tick."""
    plan, demoted = demote_unevidenced_completions(
        _plan("completed", "completed", "in_progress"), prior=None, evidence=True
    )
    assert demoted == ()
    assert _statuses(plan) == ("completed", "completed", "in_progress")


def test_finishing_the_whole_checklist_in_one_write_is_not_demoted() -> None:
    """Closing the plan after the work is sanctioned; a text-only last step has no tool."""
    plan, demoted = demote_unevidenced_completions(
        _plan("completed", "completed"), prior=_plan("completed", "in_progress"), evidence=False
    )
    assert demoted == ()
    assert plan.all_completed


def test_a_checklist_cannot_be_born_or_bulk_ticked_complete_without_evidence() -> None:
    """The all-completed exemption covers only the stored plan's active step.

    A brand-new plan submitted fully completed before any tool ran, and a write
    that completes every step while some were still pending, are demoted like
    any other unevidenced tick; only the in_progress step closes for free.
    """
    fresh, demoted = demote_unevidenced_completions(
        _plan("completed", "completed"), prior=None, evidence=False
    )
    assert demoted == ("Step 1", "Step 2")
    assert _statuses(fresh) == ("pending", "pending")

    bulk, demoted = demote_unevidenced_completions(
        _plan("completed", "completed", "completed"),
        prior=_plan("completed", "in_progress", "pending"),
        evidence=False,
    )
    assert demoted == ("Step 3",)
    assert _statuses(bulk) == ("completed", "completed", "pending")


def test_ask_user_answer_completes_only_the_step_that_was_waiting() -> None:
    """An answer is evidence for the in_progress step of a stored plan, not for a new plan."""
    session = Session()
    answered = _ask_user_turn_text()
    assert plan_evidence_available(session, prior=None, turn_user_message=answered) is False
    waiting = _plan("completed", "in_progress", "pending")
    assert plan_evidence_available(session, prior=waiting, turn_user_message=answered) is True
    mark_plan_written(session)
    # A second write on the same answer turn needs a real tool return.
    assert plan_evidence_available(session, prior=waiting, turn_user_message=answered) is False
    record_plan_evidence(session, "update_plan")
    assert plan_evidence_available(session, prior=waiting, turn_user_message=answered) is False
    record_plan_evidence(session, "scan_local_git_workspace")
    assert plan_evidence_available(session, prior=waiting, turn_user_message=answered) is True


def test_update_plan_tool_reports_reset_steps_in_its_instruction() -> None:
    session = Session()
    ctx = ActionToolScope(
        session=session,
        console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
        turn_user_message="run the demo",
    )
    result = execute_update_plan_tool(
        {
            "plan": [
                {"step": "Scan local repositories", "status": "completed"},
                {"step": "Select the repository", "status": "in_progress"},
                {"step": "Collect Actions history", "status": "pending"},
            ]
        },
        ctx,
    )
    assert result["ok"] is True
    assert "Scan local repositories" in result["instruction"]
    assert session.task_plan is not None
    assert session.task_plan.steps[0].status is PlanStepStatus.PENDING
    assert session.task_plan.steps[1].status is PlanStepStatus.IN_PROGRESS


def test_apply_update_plan_session_is_set_only_for_the_latch() -> None:
    from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session

    session = Session()
    session.plan_only_until_authorized = True
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    apply_update_plan_session(session, plan, plan_only=False)
    assert session.task_plan is plan
    assert session.plan_only_until_authorized is True


def test_apply_update_plan_session_refreshes_the_live_prompt() -> None:
    """Pinned overlay must repaint as soon as the plan is stored, not later."""
    from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session

    session = Session()
    refreshes = {"count": 0}
    session.terminal.prompt_refresh_fn = lambda: refreshes.__setitem__(
        "count", refreshes["count"] + 1
    )
    plan, _error = parse_task_plan({"plan": _PLAN})
    assert plan is not None
    apply_update_plan_session(session, plan, plan_only=True)
    assert session.task_plan is plan
    assert refreshes["count"] == 1
