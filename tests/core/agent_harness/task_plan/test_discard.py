"""Discarding a plan clears state that could leak into a later checklist."""

from types import SimpleNamespace

from core.agent_harness.task_plan import PlanStep, PlanStepStatus, TaskPlan, discard_task_plan
from core.agent_harness.task_plan.work_log import sync_task_plan_work_for_plan


def test_discard_resets_the_plan_and_every_derived_field() -> None:
    plan = TaskPlan(
        steps=(
            PlanStep("Inspect CI failures", PlanStepStatus.IN_PROGRESS),
            PlanStep("Schedule CI fixes", PlanStepStatus.PENDING),
        )
    )
    session = SimpleNamespace(
        task_plan=plan,
        task_plan_work=[["GitHub CLI · inspect checks"], []],
        task_plan_work_step_texts=tuple(item.step for item in plan.steps),
        task_plan_breakdown_emitted=True,
        plan_only_until_authorized=True,
    )

    discard_task_plan(session)

    assert session.task_plan is None
    assert session.task_plan_work == []
    assert session.task_plan_work_step_texts is None
    assert session.task_plan_breakdown_emitted is False
    assert session.plan_only_until_authorized is False

    # Re-entering the same skill must start a fresh log even with identical steps.
    session.task_plan = plan
    sync_task_plan_work_for_plan(session, plan)
    assert session.task_plan_work == [[], []]


def test_discard_does_not_add_fields_to_a_session_without_plan_state() -> None:
    session = SimpleNamespace()

    discard_task_plan(session)

    assert vars(session) == {}
