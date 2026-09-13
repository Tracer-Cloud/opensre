"""Tests for the agent update_plan tool."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from core.agent_harness.task_plan.evidence import record_plan_evidence
from core.agent_harness.task_plan.plan import (
    PlanStepStatus,
    parse_task_plan,
    task_plan_from_payload,
)
from core.agent_harness.task_plan.work_log import take_completed_plan_breakdown
from core.agent_harness.tools.tool_context import ActionToolScope
from core.domain.types.tools import ToolRole
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.update_plan import (
    execute_update_plan_tool,
    update_plan_tool,
)


def _ctx(session: Session | None = None) -> ActionToolScope:
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    return ActionToolScope(
        session=session if session is not None else Session(),
        console=console,
    )


def _worked(session: Session) -> Session:
    """A tool returned this turn, so a plan may carry completed steps."""
    record_plan_evidence(session, "shell_run")
    return session


_PLAN: list[dict[str, Any]] = [
    {"step": "Capture 502 samples from checkout", "status": "completed"},
    {"step": "Trace 502s to the last deploy", "status": "in_progress"},
    {"step": "Confirm checkout returns 2xx", "status": "pending"},
]


def test_update_plan_tool_is_action_surface_read_only() -> None:
    assert update_plan_tool.name == "update_plan"
    assert "action" in update_plan_tool.surfaces
    assert update_plan_tool.side_effect_level == "read_only"
    assert update_plan_tool.role is ToolRole.BOOKKEEPING
    explanation = update_plan_tool.input_schema["properties"]["explanation"]["description"]
    assert "investigation" in explanation.lower()
    assert "do not invent causal hypotheses" in explanation.lower()


def test_update_plan_stores_the_checklist_on_the_session() -> None:
    session = _worked(Session())
    result = execute_update_plan_tool({"plan": _PLAN}, _ctx(session=session))

    assert result["ok"] is True
    assert result["current"] == 2
    assert result["total"] == 3
    assert session.task_plan is not None
    assert session.task_plan.current_index == 2
    assert "Plan · 2/3" in result["summary"]


def test_update_plan_preserves_report_and_followup_after_verification() -> None:
    session = _worked(Session())
    labels = [
        "Scan local repositories",
        "Select a repository",
        "Collect history and references",
        "Calculate metrics and verify coverage",
        "Display the report",
        "Offer the next step",
    ]
    items = [
        {"step": label, "status": "completed" if index < 4 else "pending"}
        for index, label in enumerate(labels)
    ]
    items[4]["status"] = "in_progress"
    result = execute_update_plan_tool({"plan": items}, _ctx(session=session))

    assert result["ok"] is True
    assert result["plan"] == items
    assert result["summary"].splitlines()[-2:] == [
        "  ● Display the report",
        "  ○ Offer the next step",
    ]
    assert session.task_plan is not None
    assert [step.step for step in session.task_plan.steps] == labels

    # More work lands, the menu step becomes active, then the whole checklist
    # closes: the active last step may close without a tool of its own.
    items[4]["status"] = "completed"
    items[5]["status"] = "in_progress"
    result = execute_update_plan_tool({"plan": items}, _ctx(session=_worked(session)))
    assert result["ok"] is True
    completed = [{"step": label, "status": "completed"} for label in labels]
    result = execute_update_plan_tool({"plan": completed}, _ctx(session=session))
    assert result["ok"] is True
    breakdown = take_completed_plan_breakdown(session)
    assert breakdown.startswith("Plan complete · 6/6")
    assert breakdown.splitlines()[-1] == "  ✓ Offer the next step"


def test_update_plan_rejects_two_in_progress_steps() -> None:
    session = Session()
    result = execute_update_plan_tool(
        {
            "plan": [
                {"step": "First", "status": "in_progress"},
                {"step": "Second", "status": "in_progress"},
            ],
            "plan_only": True,
        },
        _ctx(session=session),
    )
    assert result["ok"] is False
    assert "at most one" in result["error"]
    assert session.task_plan is None
    assert session.plan_only_until_authorized is False


def test_update_plan_stores_explanation_and_revises_in_place() -> None:
    # Arrange: an initial two-step plan with a first diagnosis.
    session = Session()
    initial: list[dict[str, Any]] = [
        {"step": "Reproduce the 502 on checkout", "status": "in_progress"},
        {"step": "Confirm checkout returns 2xx", "status": "pending"},
    ]
    first = execute_update_plan_tool(
        {"plan": initial, "explanation": "first diagnosis"},
        _ctx(session=session),
    )
    assert first["ok"] is True
    assert session.task_plan is not None
    assert session.task_plan.total == 2
    assert session.task_plan.explanation == "first diagnosis"

    # Act: after more work, a second call revises to three advanced steps and a
    # new diagnosis.
    _worked(session)
    revised = [
        {"step": "Capture 502 samples from checkout", "status": "completed"},
        {"step": "Trace 502s to the last deploy", "status": "completed"},
        {"step": "Confirm checkout returns 2xx", "status": "in_progress"},
    ]
    second = execute_update_plan_tool(
        {"plan": revised, "explanation": "moved to verify"},
        _ctx(session=session),
    )

    # Assert: the session holds only the revision — replaced, not merged.
    assert second["ok"] is True
    assert session.task_plan is not None
    assert session.task_plan.total == 3
    assert session.task_plan.current_index == 3
    assert session.task_plan.steps[0].status is PlanStepStatus.COMPLETED
    assert session.task_plan.explanation == "moved to verify"


def test_update_plan_tool_name_is_the_action_enum() -> None:
    from tools.interactive_shell.action_names import ActionToolName

    assert update_plan_tool.name == ActionToolName.UPDATE_PLAN


# --- create → mark-complete flow --------------------------------------------


def test_update_plan_marks_a_fully_completed_plan_as_terminal() -> None:
    # Arrange / Act: every step completed (the verification step last), written
    # after the work ran this turn.
    session = _worked(Session())
    done: list[dict[str, Any]] = [
        {"step": "Capture 502 samples from checkout", "status": "completed"},
        {"step": "Trace 502s to the last deploy", "status": "completed"},
        {"step": "Confirm checkout returns 2xx", "status": "completed"},
    ]
    result = execute_update_plan_tool({"plan": done}, _ctx(session=session))

    # Assert: the focused index sits on the final step and no step reopens.
    assert result["ok"] is True
    assert session.task_plan is not None
    assert session.task_plan.all_completed is True
    assert session.task_plan.current_index == session.task_plan.total == 3
    assert "Plan · 3/3" in result["summary"]
    # A terminal plan is neither plan-only nor a freshly authorized execution.
    assert "Plan-only" not in result["instruction"]
    assert "Execution is authorized" not in result["instruction"]


def test_update_plan_does_not_accept_a_plan_born_complete_before_any_work() -> None:
    # Arrange / Act: the same fully completed plan, but no tool has run this turn.
    session = Session()
    done: list[dict[str, Any]] = [
        {"step": "Capture 502 samples from checkout", "status": "completed"},
        {"step": "Trace 502s to the last deploy", "status": "completed"},
        {"step": "Confirm checkout returns 2xx", "status": "completed"},
    ]
    result = execute_update_plan_tool({"plan": done}, _ctx(session=session))

    # Assert: the write lands, but every unearned tick is reopened.
    assert result["ok"] is True
    assert session.task_plan is not None
    assert session.task_plan.all_completed is False
    assert all(step.status is not PlanStepStatus.COMPLETED for step in session.task_plan.steps)


def test_update_plan_blocked_steps_settle_the_plan_without_evidence_or_completion() -> None:
    """Regression: work a runtime cannot do must not need fake tool runs to close.

    Observed live: a skill's capability gate failed after only ``skill_view``
    (bookkeeping, no evidence), the host kept nudging while steps were pending,
    and the model ran unrelated ``/loops`` commands to earn ``completed`` marks
    for work that never happened — ending on ``Plan complete · 9/9``.
    """
    # Arrange: the gate step is in progress; nothing but bookkeeping ran.
    session = Session()
    ctx = _ctx(session=session)
    opened: list[dict[str, Any]] = [
        {"step": "Verify runtime support for scheduled repairs", "status": "in_progress"},
        {"step": "Confirm repair authorization", "status": "pending"},
        {"step": "Create the repair loop", "status": "pending"},
        {"step": "Report the outcome", "status": "pending"},
    ]
    assert execute_update_plan_tool({"plan": opened}, ctx)["ok"] is True

    # Act 1: the gate finds blockers. Finding them is the gate's outcome, so
    # it closes without a tool return; dependent steps are blocked, not done.
    explanation = "Unattended turns are read-only; no per-PR deadline storage."
    gated: list[dict[str, Any]] = [
        {"step": "Verify runtime support for scheduled repairs", "status": "completed"},
        {"step": "Confirm repair authorization", "status": "blocked"},
        {"step": "Create the repair loop", "status": "blocked"},
        {"step": "Report the outcome", "status": "in_progress"},
    ]
    gate_result = execute_update_plan_tool({"plan": gated, "explanation": explanation}, ctx)
    assert gate_result["ok"] is True
    assert session.task_plan is not None
    assert session.task_plan.steps[0].status is PlanStepStatus.COMPLETED
    assert "Reset to pending" not in gate_result["instruction"]
    assert "Blocked steps stay blocked" in gate_result["instruction"]

    # Act 2: the text-only report closes the plan.
    reported = [*gated[:-1], {"step": "Report the outcome", "status": "completed"}]
    result = execute_update_plan_tool({"plan": reported, "explanation": explanation}, ctx)

    # Assert: the plan is settled (no nudge to continue), blocked steps stay
    # blocked, and no header claims 4/4.
    assert result["ok"] is True
    plan = session.task_plan
    assert plan is not None
    assert plan.is_settled and not plan.all_completed
    assert [step.status for step in plan.steps] == [
        PlanStepStatus.COMPLETED,
        PlanStepStatus.BLOCKED,
        PlanStepStatus.BLOCKED,
        PlanStepStatus.COMPLETED,
    ]
    assert "Plan · 2/4 · 2 blocked" in result["summary"]
    assert "Continue the in_progress step now" not in result["instruction"]
    breakdown = take_completed_plan_breakdown(session)
    assert breakdown.startswith("Plan ended · 2/4 completed · 2 blocked")
    assert "Plan complete" not in breakdown


def test_update_plan_gate_read_from_a_skill_reference_earns_the_gate_step() -> None:
    """Regression: the one-write capability gate a skill prescribes must land.

    Observed live: after ``skill_view(reference="runtime")`` the model wrote a
    fresh nine-step plan with step 1 completed, 2–8 blocked, and 9 in_progress.
    The reference read counted as bookkeeping, step 1 was reset to pending, the
    overlay read ``Plan · 9/9`` over zero completed steps, and the model looped
    in_progress → re-read → completed → reset three times before giving up and
    marking the verified step blocked.
    """
    # Arrange: the previous skill's settled plan is still stored; this turn only
    # loaded the new skill body and its runtime reference.
    session = Session()
    ctx = _ctx(session=session)
    handoff: list[dict[str, Any]] = [
        {"step": "Deliver the reliability report", "status": "completed"},
        {"step": "Offer repair, Slack, or finish options", "status": "blocked"},
    ]
    assert execute_update_plan_tool({"plan": handoff, "explanation": "menu blocked"}, ctx)["ok"]
    record_plan_evidence(session, "skill_view", {"name": "scheduling-github-ci-fixes"})
    record_plan_evidence(
        session, "skill_view", {"name": "scheduling-github-ci-fixes", "reference": "runtime"}
    )

    # Act: the gate write the skill asks for, in one call.
    steps = [
        "Verify runtime support for scheduled repairs",
        "Discover candidate repositories",
        "Select repository",
        "Confirm repair authorization",
        "Offer the demo",
        "Run the demo",
        "Create the repair loop",
        "Verify a scheduled execution",
        "Display setup outcome",
    ]
    gated = [{"step": step, "status": "blocked"} for step in steps]
    gated[0]["status"] = "completed"
    gated[-1]["status"] = "in_progress"
    result = execute_update_plan_tool(
        {"plan": gated, "explanation": "Unattended turns are read-only."}, ctx
    )

    # Assert: the reference read is the gate step's work, so it stays completed,
    # and the live header counts completed work rather than the focused index.
    assert result["ok"] is True
    assert "Reset to pending" not in result["instruction"]
    assert session.task_plan is not None
    assert session.task_plan.steps[0].status is PlanStepStatus.COMPLETED
    assert session.task_plan.steps[-1].status is PlanStepStatus.IN_PROGRESS
    assert result["summary"].startswith("Plan · 1/9 · 7 blocked")
    assert "Plan · 9/9" not in result["summary"]


def test_update_plan_blocking_steps_does_not_bulk_tick_pending_work() -> None:
    """The blocked-write exemption closes only the step that was in progress."""
    session = Session()
    ctx = _ctx(session=session)
    opened: list[dict[str, Any]] = [
        {"step": "Verify runtime support", "status": "in_progress"},
        {"step": "Select the repository", "status": "pending"},
        {"step": "Create the repair loop", "status": "pending"},
    ]
    assert execute_update_plan_tool({"plan": opened}, ctx)["ok"] is True
    overreach: list[dict[str, Any]] = [
        {"step": "Verify runtime support", "status": "completed"},
        {"step": "Select the repository", "status": "completed"},
        {"step": "Create the repair loop", "status": "blocked"},
    ]
    result = execute_update_plan_tool(
        {"plan": overreach, "explanation": "Loop blocked: unattended turns are read-only."}, ctx
    )
    assert result["ok"] is True
    assert session.task_plan is not None
    assert [step.status for step in session.task_plan.steps] == [
        PlanStepStatus.COMPLETED,
        PlanStepStatus.IN_PROGRESS,
        PlanStepStatus.BLOCKED,
    ]
    assert "Select the repository" in result["instruction"]


def test_update_plan_rejects_blocked_steps_without_a_named_blocker() -> None:
    session = Session()
    blocked: list[dict[str, Any]] = [
        {"step": "Verify runtime support", "status": "completed"},
        {"step": "Create the repair loop", "status": "blocked"},
    ]
    result = execute_update_plan_tool({"plan": blocked}, _ctx(session=session))
    assert result["ok"] is False
    assert "blocker" in result["error"]
    assert session.task_plan is None


def test_update_plan_normal_create_carries_only_the_base_instruction() -> None:
    # A plain create (no plan_only, no Ask User answers on the turn) must not
    # emit the plan-only or execution-authorized suffixes; incomplete plans get
    # a continue nudge so the model does not idle with pending steps.
    session = _worked(Session())
    result = execute_update_plan_tool({"plan": _PLAN}, _ctx(session=session))

    assert result["ok"] is True
    assert "Plan stored." in result["instruction"]
    assert "Plan-only" not in result["instruction"]
    assert "Execution is authorized" not in result["instruction"]
    assert "Continue the in_progress step now" in result["instruction"]


def test_update_plan_promotes_next_pending_when_model_leaves_a_gap() -> None:
    """Completed + pending with no in_progress must not idle as Plan · 2/3 ○ ○."""
    session = _worked(Session())
    gapped: list[dict[str, Any]] = [
        {"step": "Confirm checkout latency telemetry source", "status": "completed"},
        {"step": "Query recent checkout latency", "status": "pending"},
        {"step": "Verify findings against baseline", "status": "pending"},
    ]
    result = execute_update_plan_tool({"plan": gapped}, _ctx(session=session))

    assert result["ok"] is True
    assert session.task_plan is not None
    assert session.task_plan.steps[0].status is PlanStepStatus.COMPLETED
    assert session.task_plan.steps[1].status is PlanStepStatus.IN_PROGRESS
    assert session.task_plan.steps[2].status is PlanStepStatus.PENDING
    assert session.task_plan.current_index == 2
    assert "● Query recent checkout latency" in result["summary"]
    assert "Continue the in_progress step now" in result["instruction"]


def test_update_plan_result_payload_is_a_reparseable_durable_record() -> None:
    # The tool result doubles as the durable CURRENT PLAN record: it must parse
    # back into an equivalent plan when older messages drop from context.
    session = _worked(Session())
    result = execute_update_plan_tool({"plan": _PLAN}, _ctx(session=session))

    restored = task_plan_from_payload(result)
    reparsed, error = parse_task_plan(result)
    assert error is None
    assert restored is not None and reparsed is not None
    assert [step.step for step in restored.steps] == [item["step"] for item in _PLAN]
    assert restored.current_index == reparsed.current_index == 2
