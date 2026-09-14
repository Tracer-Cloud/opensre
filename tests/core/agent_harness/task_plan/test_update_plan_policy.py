"""Tests for update_plan host policy."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    format_ask_user_answers,
)
from core.agent_harness.task_plan.completion import demote_unevidenced_completions
from core.agent_harness.task_plan.evidence import (
    mark_plan_written,
    plan_evidence_available,
    record_plan_evidence,
)
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan, parse_task_plan
from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_host_policy
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


def _verified_plan(*statuses: str, verify: int) -> TaskPlan:
    """``_plan`` with step ``verify`` (1-based) declared as the verification step."""
    items = [{"step": f"Step {i}", "status": s} for i, s in enumerate(statuses, 1)]
    items[verify - 1]["verifies"] = True
    plan, error = parse_task_plan({"plan": items})
    assert error is None and plan is not None
    return plan


def _demoted(*args: Any, **kwargs: Any) -> tuple[TaskPlan, tuple[str, ...]]:
    check = demote_unevidenced_completions(*args, **kwargs)
    return check.plan, check.demoted


def test_completions_without_tool_evidence_are_reset_to_pending() -> None:
    """The demo run: two ``update_plan`` writes ticked steps whose tools had not run yet.

    Write 1 (no prior plan, no tool yet) marked step 2 completed. Write 2 came after
    one tool returned and ticked steps 1-3, though only step 3 had been in_progress:
    a pending step cannot have been worked, so steps 1 and 2 are reset; step 3 keeps
    its tick because work returned while it was the active step.
    """
    first, demoted = _demoted(
        _plan("pending", "completed", "in_progress", "pending"), prior=None, evidence=False
    )
    assert demoted == ("Step 2",)
    assert _statuses(first) == ("pending", "pending", "in_progress", "pending")

    second, demoted = _demoted(
        _plan("completed", "completed", "completed", "in_progress"), prior=first, evidence=True
    )
    assert demoted == ("Step 1", "Step 2")
    assert _statuses(second) == ("pending", "pending", "completed", "in_progress")


def test_plan_written_after_the_work_keeps_its_completed_steps() -> None:
    """A retroactive plan (tools ran, then update_plan) is not a false tick."""
    plan, demoted = _demoted(
        _plan("completed", "completed", "in_progress"), prior=None, evidence=True
    )
    assert demoted == ()
    assert _statuses(plan) == ("completed", "completed", "in_progress")


def test_finishing_the_whole_checklist_in_one_write_is_not_demoted() -> None:
    """Closing the plan after the work is sanctioned once a verification step has run.

    A text-only last step has no tool of its own; it closes for free because
    the plan's check already completed on its own evidence.
    """
    # Arrange: step 1 is the check and completed earlier; step 2 is the summary.
    prior = _verified_plan("completed", "in_progress", verify=1)

    # Act
    plan, demoted = _demoted(
        _verified_plan("completed", "completed", verify=1), prior=prior, evidence=False
    )

    # Assert
    assert demoted == ()
    assert plan.all_completed and plan.verified


def test_a_text_only_close_without_a_verification_step_is_reset() -> None:
    """The adversarial run: list, count, summarize — the summary closed with nothing checked."""
    # Arrange: two steps done with tools, the summary active, no step verifies.
    prior = _plan("completed", "completed", "in_progress")

    # Act: the summary is completed with no tool return since the last write.
    check = demote_unevidenced_completions(
        _plan("completed", "completed", "completed"), prior=prior, evidence=False
    )

    # Assert: the summary is reset and the reason is named for the tool result.
    assert check.demoted == ("Step 3",)
    assert _statuses(check.plan) == ("completed", "completed", "pending")
    assert check.closed_unverified is True


def test_a_verification_step_never_closes_for_free() -> None:
    """A check that ran nothing checked nothing, even when it is the closing step."""
    # Arrange: the check is the last step and is active; no tool returned.
    prior = _verified_plan("completed", "in_progress", verify=2)

    # Act
    check = demote_unevidenced_completions(
        _verified_plan("completed", "completed", verify=2), prior=prior, evidence=False
    )

    # Assert: reset, and not reported as the unverified-close case (it is the check itself).
    assert check.demoted == ("Step 2",)
    assert check.closed_unverified is False
    # With its own evidence it completes, and the plan is verified.
    passed = demote_unevidenced_completions(
        _verified_plan("completed", "completed", verify=2), prior=prior, evidence=True
    )
    assert passed.demoted == () and passed.plan.verified


def test_a_checklist_cannot_be_born_or_bulk_ticked_complete_without_evidence() -> None:
    """A brand-new plan submitted fully completed before any tool ran, and a write
    that completes every step while some were still pending, are demoted like
    any other unevidenced tick. The active step closes for free only once a
    verification step has run, so here it is reset too.
    """
    fresh, demoted = _demoted(_plan("completed", "completed"), prior=None, evidence=False)
    assert demoted == ("Step 1", "Step 2")
    assert _statuses(fresh) == ("pending", "pending")

    bulk, demoted = _demoted(
        _plan("completed", "completed", "completed"),
        prior=_plan("completed", "in_progress", "pending"),
        evidence=False,
    )
    assert demoted == ("Step 2", "Step 3")
    assert _statuses(bulk) == ("completed", "pending", "pending")


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


def test_loading_a_skill_body_is_bookkeeping_but_reading_its_reference_is_work() -> None:
    """A skill step whose only tool is ``skill_view(reference=…)`` must be completable."""
    session = Session()
    mark_plan_written(session)
    record_plan_evidence(session, "skill_view", {"name": "scheduling-github-ci-fixes"})
    record_plan_evidence(session, "skill_view", {"name": "x", "reference": "  "})
    assert plan_evidence_available(session, prior=None, turn_user_message="") is False
    record_plan_evidence(
        session, "skill_view", {"name": "scheduling-github-ci-fixes", "reference": "runtime"}
    )
    assert plan_evidence_available(session, prior=None, turn_user_message="") is True


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


def test_a_slash_command_is_evidence_for_its_step_but_not_work_for_the_plan_rule() -> None:
    """Skill B's loop steps are `/cron add`, `/cron run`, `/cron list`: they must complete.

    The shell's own commands never require a plan, so they are not work for
    the second-work-tool rule; a step that consists of one still earns its
    tick from the command's return.
    """
    from core.agent_harness.task_plan.evidence import work_returns_this_turn

    # Arrange: a plan was written, then one slash command returned.
    session = Session()
    mark_plan_written(session)
    record_plan_evidence(
        session, "slash_invoke", {"command": "/cron", "args": ["add"]}, details={"ok": True}
    )

    # Act / Assert
    assert plan_evidence_available(session, prior=None, turn_user_message="") is True
    assert work_returns_this_turn(session) == 0


def test_a_deliverable_step_completes_on_its_reply_but_not_as_the_closing_step() -> None:
    """Demo A: the report step is marked done in the response that opens the menu.

    Its work is the reply itself, so it has no tool to show; without this it
    was reset every run and the plan ended blocked instead of complete.
    """

    # Arrange: the report step is active and flagged; the menu step follows.
    def _items(report: str, offer: str) -> list[dict[str, Any]]:
        return [
            {"step": "Collect metrics", "status": "completed"},
            {"step": "Show the report", "status": report, "deliverable": True},
            {"step": "Offer the next step", "status": offer},
        ]

    prior, _ = parse_task_plan({"plan": _items("in_progress", "pending")})
    written, _ = parse_task_plan({"plan": _items("completed", "in_progress")})
    assert prior is not None and written is not None

    # Act
    mid_plan = demote_unevidenced_completions(written, prior=prior, evidence=False)

    # Assert: the report step keeps its tick while work remains.
    assert mid_plan.demoted == ()

    # The same flag on a closing step does not bypass verification.
    last_prior, _ = parse_task_plan(
        {
            "plan": [
                {"step": "Count", "status": "completed"},
                {"step": "Summarize", "status": "in_progress", "deliverable": True},
            ]
        }
    )
    last_written, _ = parse_task_plan(
        {
            "plan": [
                {"step": "Count", "status": "completed"},
                {"step": "Summarize", "status": "completed", "deliverable": True},
            ]
        }
    )
    assert last_prior is not None and last_written is not None
    closing = demote_unevidenced_completions(last_written, prior=last_prior, evidence=False)
    assert closing.demoted == ("Summarize",) and closing.closed_unverified is True
