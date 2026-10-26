"""Which turns count as demo turns, and when a background extraction pass is due."""

from __future__ import annotations

import pytest

from config.constants.skills import (
    ONBOARDING_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)
from core.agent_harness.session import memory_turns
from core.agent_harness.session.memory_turns import (
    EXTRACTION_TURN_INTERVAL,
    demo_turns,
    note_recorded_turn,
    turn_is_demo,
)
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.task_plan.evidence import mark_plan_written, reset_plan_evidence
from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan


def _settled_plan(owner: str | None) -> TaskPlan:
    return TaskPlan(
        steps=(PlanStep(step="Repair the demo PR", status=PlanStepStatus.COMPLETED),),
        owner=owner,
    )


@pytest.mark.parametrize(
    ("active_skill", "expected"),
    [
        (ONBOARDING_SKILL_NAME, True),
        (SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME, True),
        ("repair-github-ci", False),
        (None, False),
    ],
)
def test_the_active_skill_decides_a_demo_turn(active_skill: str | None, expected: bool) -> None:
    session = SessionCore()
    session.active_skill = active_skill
    reset_plan_evidence(session)

    assert turn_is_demo(session) is expected


def test_the_demo_turn_that_settles_its_plan_and_releases_the_skill_still_counts() -> None:
    """A demo's last step settles its plan, and the host clears the skill that turn."""
    session = SessionCore()
    reset_plan_evidence(session)
    session.task_plan = _settled_plan(SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME)
    mark_plan_written(session)
    session.active_skill = None

    assert turn_is_demo(session)


def test_a_settled_demo_plan_left_over_from_earlier_does_not_taint_later_turns() -> None:
    session = SessionCore()
    session.task_plan = _settled_plan(SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME)
    reset_plan_evidence(session)  # a new turn that did not touch the plan

    assert not turn_is_demo(session)


def test_only_non_demo_turns_complete_the_extraction_interval() -> None:
    session_id = "interval-session"
    due = [
        note_recorded_turn(session_id, demo=index % 2 == 1, turn_id=f"t{index}", user_text="q")
        for index in range(2 * EXTRACTION_TURN_INTERVAL)
    ]

    assert due.count(True) == 1
    assert due.index(True) == 2 * EXTRACTION_TURN_INTERVAL - 2
    demo = demo_turns(session_id)
    assert demo.turn_ids == {f"t{index}" for index in range(1, 2 * EXTRACTION_TURN_INTERVAL, 2)}
    assert demo.latest_is_demo
    memory_turns.forget_session(session_id)
    assert demo_turns(session_id).turn_ids == frozenset()
