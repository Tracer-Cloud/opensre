"""A ``deliverable`` plan step is the only thing that lets a mid-plan reply show."""

from __future__ import annotations

from typing import Any

from core.agent_harness.task_plan.plan import (
    TaskPlan,
    parse_task_plan,
    task_plan_from_payload,
    task_plan_to_payload,
)
from core.agent_harness.task_plan.update_plan_policy import demote_unevidenced_completions


def _plan(statuses: list[str], *, deliverable_index: int = 3) -> TaskPlan:
    items: list[dict[str, Any]] = [
        {"step": f"Step {i + 1}", "status": status} for i, status in enumerate(statuses)
    ]
    items[deliverable_index]["deliverable"] = True
    plan, error = parse_task_plan({"plan": items})
    assert error is None and plan is not None
    return plan


def test_awaits_reply_only_when_the_deliverable_is_current_or_next() -> None:
    # Work still open before the report step: a text-only reply is premature.
    assert (
        _plan(["completed", "in_progress", "pending", "pending", "pending"]).awaits_reply is False
    )
    # The report step is next after the step in progress, or is itself in progress.
    assert _plan(["completed", "completed", "in_progress", "pending", "pending"]).awaits_reply
    assert _plan(["completed", "completed", "completed", "in_progress", "pending"]).awaits_reply
    # No step in progress: only the first pending step counts.
    assert _plan(["completed", "completed", "completed", "pending", "pending"]).awaits_reply
    assert _plan(["completed", "completed", "pending", "pending", "pending"]).awaits_reply is False
    # A plan without the flag never asks for a reply to be shown.
    unflagged, error = parse_task_plan(
        {"plan": [{"step": "a", "status": "in_progress"}, {"step": "b", "status": "pending"}]}
    )
    assert error is None and unflagged is not None
    assert unflagged.awaits_reply is False


def test_deliverable_flag_survives_payload_round_trip_and_status_demotion() -> None:
    plan = _plan(["completed", "completed", "completed", "completed", "pending"])
    payload = task_plan_to_payload(plan)
    assert [item.get("deliverable") for item in payload["plan"]] == [None, None, None, True, None]
    restored = task_plan_from_payload(payload)
    assert restored is not None
    assert [item.deliverable for item in restored.steps] == [False, False, False, True, False]

    demoted, names = demote_unevidenced_completions(plan, prior=None, evidence=False)
    assert names
    assert [item.deliverable for item in demoted.steps] == [False, False, False, True, False]
