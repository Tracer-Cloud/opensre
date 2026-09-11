"""TaskPlan parse/validate and progress counter."""

from __future__ import annotations

from core.agent_harness.task_plan.plan import (
    PlanStepStatus,
    parse_task_plan,
    task_plan_from_payload,
    task_plan_to_payload,
)
from core.agent_harness.task_plan.progress import format_task_plan_plain


def _items(*statuses: str) -> list[dict[str, str]]:
    labels = [
        "Capture 502 samples from checkout",
        "Trace 502s to the last deploy",
        "Confirm checkout returns 2xx",
    ]
    return [{"step": labels[index], "status": status} for index, status in enumerate(statuses)]


def test_parse_rejects_a_single_step() -> None:
    plan, error = parse_task_plan({"plan": [{"step": "fix it", "status": "in_progress"}]})
    assert plan is None
    assert error is not None
    assert "at least two" in error


def test_parse_rejects_two_in_progress_steps() -> None:
    plan, error = parse_task_plan({"plan": _items("in_progress", "in_progress", "pending")})
    assert plan is None
    assert error is not None
    assert "at most one" in error


def test_parse_accepts_a_verifiable_plan() -> None:
    plan, error = parse_task_plan({"plan": _items("completed", "in_progress", "pending")})
    assert error is None
    assert plan is not None
    assert plan.current_index == 2
    assert plan.total == 3
    assert plan.steps[-1].status is PlanStepStatus.PENDING
    text = format_task_plan_plain(plan)
    assert text.startswith("Plan · 2/3")
    assert "✓" in text and "●" in text and "○" in text


def test_payload_round_trips() -> None:
    plan, error = parse_task_plan(
        {"plan": _items("pending", "pending", "pending"), "explanation": "draft"}
    )
    assert error is None and plan is not None
    assert plan.all_pending is True
    restored = task_plan_from_payload(task_plan_to_payload(plan))
    assert restored == plan
    assert format_task_plan_plain(plan).startswith("Plan ready · 0/3 executed")


def test_parse_strips_terminal_controls_from_steps_and_explanation() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "\x1b]0;pwn\x07Capture samples", "status": "in_progress"},
                {"step": "Verify\x07 recovery", "status": "pending"},
            ],
            "explanation": "why\x1b[0m now",
        }
    )
    assert error is None and plan is not None
    assert "\x1b" not in plan.steps[0].step
    assert "\x07" not in plan.steps[0].step
    assert "Capture samples" in plan.steps[0].step
    assert "\x07" not in plan.steps[1].step
    assert "\x1b" not in plan.explanation
    assert "why" in plan.explanation


# --- parse_task_plan: every remaining rejection branch ------------------------


def test_parse_rejects_a_non_list_plan() -> None:
    # Arrange / Act: the plan field is not a list of steps.
    for bad in (None, "just do it", {"step": "x", "status": "pending"}):
        plan, error = parse_task_plan({"plan": bad})
        # Assert: rejected with the two-step message (the list guard).
        assert plan is None
        assert error is not None and "at least two" in error


def test_parse_rejects_a_non_object_item() -> None:
    plan, error = parse_task_plan({"plan": ["do a thing", {"step": "verify", "status": "pending"}]})
    assert plan is None
    assert "object with step and status" in (error or "")


def test_parse_rejects_an_empty_step() -> None:
    plan, error = parse_task_plan(
        {"plan": [{"step": "   ", "status": "pending"}, {"step": "verify", "status": "pending"}]}
    )
    assert plan is None
    assert "non-empty step" in (error or "")


def test_parse_rejects_an_unknown_status() -> None:
    plan, error = parse_task_plan(
        {"plan": [{"step": "do it", "status": "done"}, {"step": "verify", "status": "pending"}]}
    )
    assert plan is None
    assert "pending, in_progress, or completed" in (error or "")


def test_parse_rejects_completing_the_final_step_while_another_runs() -> None:
    # The final step is completed while an earlier step is still in_progress —
    # a distinct rule from "more than one in_progress".
    plan, error = parse_task_plan({"plan": _items("in_progress", "completed")})
    assert plan is None
    assert "cannot complete the final step" in (error or "")


def test_parse_ignores_a_non_string_explanation() -> None:
    plan, error = parse_task_plan({"plan": _items("pending", "pending"), "explanation": 123})
    assert error is None and plan is not None
    assert plan.explanation == ""


# --- TaskPlan progress properties: every focus/count state -------------------


def test_focused_step_prefers_the_in_progress_step() -> None:
    plan, _ = parse_task_plan({"plan": _items("completed", "in_progress", "pending")})
    assert plan is not None
    assert plan.focused_step.status is PlanStepStatus.IN_PROGRESS
    assert plan.current_index == 2


def test_focused_step_falls_back_to_the_first_pending_step() -> None:
    plan, _ = parse_task_plan({"plan": _items("completed", "pending", "pending")})
    assert plan is not None
    assert plan.focused_step.status is PlanStepStatus.PENDING
    assert plan.current_index == 2


def test_focused_step_is_the_last_step_when_all_completed() -> None:
    plan, _ = parse_task_plan({"plan": _items("completed", "completed", "completed")})
    assert plan is not None
    assert plan.all_completed
    assert plan.focused_step is plan.steps[-1]
    assert plan.current_index == plan.total == 3


def test_all_pending_and_all_completed_counts_are_exclusive() -> None:
    pending, _ = parse_task_plan({"plan": _items("pending", "pending")})
    assert pending is not None
    assert pending.all_pending and not pending.all_completed
    assert pending.completed_count == 0

    done, _ = parse_task_plan({"plan": _items("completed", "completed")})
    assert done is not None
    assert done.all_completed and not done.all_pending
    assert done.completed_count == done.total == 2


def test_parse_accepts_a_seven_step_plan() -> None:
    items = [{"step": f"Step {index} outcome", "status": "pending"} for index in range(7)]
    items[-1]["step"] = "Confirm the check passed"
    plan, error = parse_task_plan({"plan": items})
    assert error is None and plan is not None
    assert plan.total == 7
    assert plan.all_pending is True


def test_payload_round_trip_drops_unknown_keys() -> None:
    plan, error = parse_task_plan({"plan": _items("pending", "pending")})
    assert error is None and plan is not None
    payload = task_plan_to_payload(plan)
    payload["plan_only_until_authorized"] = True
    payload["extra"] = "ignored"
    restored = task_plan_from_payload(payload)
    assert restored == plan


def test_from_payload_rejects_garbage() -> None:
    assert task_plan_from_payload(None) is None
    assert task_plan_from_payload([]) is None
    assert task_plan_from_payload({"plan": [{"step": "only one", "status": "pending"}]}) is None
