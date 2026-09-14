"""Host-owned plan work log and post-execution breakdown."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_harness.task_plan.plan import parse_task_plan
from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session
from core.agent_harness.task_plan.work_log import (
    format_task_plan_breakdown,
    record_task_plan_work,
    take_completed_plan_breakdown,
)


def _session_with_plan(*statuses: str):
    labels = [
        "Gather evidence",
        "Rank hypotheses",
        "Propose fix",
        "Verify recovery",
    ]
    items = [{"step": labels[i], "status": status} for i, status in enumerate(statuses)]
    plan, error = parse_task_plan({"plan": items})
    assert error is None and plan is not None
    session = SimpleNamespace(
        task_plan=None,
        task_plan_work=[],
        task_plan_work_step_texts=None,
        task_plan_breakdown_emitted=False,
        plan_only_until_authorized=False,
        terminal=None,
    )
    apply_update_plan_session(session, plan, plan_only=False)
    return session


def test_record_task_plan_work_attributes_to_in_progress_step() -> None:
    session = _session_with_plan("in_progress", "pending", "pending", "pending")
    record_task_plan_work(session, "Execute curl -sI checkout")
    record_task_plan_work(session, "PostHog · query exceptions")
    assert session.task_plan_work[0] == [
        "Execute curl -sI checkout",
        "PostHog · query exceptions",
    ]
    assert session.task_plan_work[1] == []


def test_record_task_plan_work_honors_explicit_step_index() -> None:
    session = _session_with_plan("in_progress", "pending", "pending", "pending")
    record_task_plan_work(session, "Loading integrations", step_index=0)
    record_task_plan_work(session, "PostHog · exceptions", step_index=1)
    record_task_plan_work(session, "Diagnosing", step_index=2)
    assert session.task_plan_work[0] == ["Loading integrations"]
    assert session.task_plan_work[1] == ["PostHog · exceptions"]
    assert session.task_plan_work[2] == ["Diagnosing"]
    assert session.task_plan_work[3] == []


def test_record_skips_when_no_in_progress_step() -> None:
    session = _session_with_plan("pending", "pending", "pending", "pending")
    record_task_plan_work(session, "should not land")
    assert all(bucket == [] for bucket in session.task_plan_work)


def test_format_breakdown_lists_work_under_steps() -> None:
    session = _session_with_plan("completed", "completed", "completed", "completed")
    session.task_plan_work = [
        ["call posthog tool"],
        ["sentry_issues tool call"],
        [],
        ["verify recovery"],
    ]
    text = format_task_plan_breakdown(session.task_plan, session.task_plan_work)
    assert text.startswith("Plan complete · 4/4")
    assert "↳ call posthog tool" in text
    assert "↳ sentry_issues tool call" in text


def test_format_breakdown_groups_same_kind_work_into_a_count() -> None:
    session = _session_with_plan("completed", "completed", "completed", "completed")
    session.task_plan_work = [
        [
            "GitHub CLI gh -R Tracer-Cloud/opensre repo view --json name,description",
            "GitHub CLI gh -R Tracer-Cloud/opensre pr list --state open --limit 20",
            "GitHub CLI gh -R Tracer-Cloud/opensre issue list --state open",
        ],
        ["Execute make test"],
        [],
        [],
    ]
    text = format_task_plan_breakdown(session.task_plan, session.task_plan_work)
    # Three GitHub CLI calls collapse to one grouped summary, not three lines.
    assert "↳ GitHub CLI · 3 calls" in text
    assert text.count("↳ GitHub CLI") == 1
    assert "repo view" not in text  # verbose commands hidden by the grouping
    # A lone call keeps its concrete (trimmed) text.
    assert "↳ Execute make test" in text


def test_take_completed_plan_breakdown_is_one_shot() -> None:
    session = _session_with_plan("completed", "in_progress", "pending", "pending")
    record_task_plan_work(session, "mid work")
    # Complete the plan.
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Gather evidence", "status": "completed"},
                {"step": "Rank hypotheses", "status": "completed"},
                {"step": "Propose fix", "status": "completed"},
                {"step": "Verify recovery", "status": "completed"},
            ]
        }
    )
    assert error is None and plan is not None
    apply_update_plan_session(session, plan, plan_only=False)
    first = take_completed_plan_breakdown(session)
    assert "Plan complete · 4/4" in first
    assert "↳ mid work" in first
    assert take_completed_plan_breakdown(session) == ""


def test_new_checklist_resets_breakdown_latch() -> None:
    session = _session_with_plan("completed", "completed", "completed", "completed")
    assert take_completed_plan_breakdown(session)
    assert session.task_plan_breakdown_emitted is True
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Different step A", "status": "in_progress"},
                {"step": "Different step B", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    apply_update_plan_session(session, plan, plan_only=False)
    assert session.task_plan_breakdown_emitted is False
    assert session.task_plan_work == [[], []]
