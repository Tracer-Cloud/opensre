"""Flush / restore the live :class:`TaskPlan` so resume keeps the checklist."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.agent_harness.task_plan.plan import (
    TaskPlan,
    task_plan_from_payload,
    task_plan_to_payload,
)

TASK_PLAN_STATE_CUSTOM_TYPE = "task_plan_state"


def task_plan_state_snapshot(session: Any) -> dict[str, Any] | None:
    """Flush payload for the session's live task plan, or ``None`` when empty.

    Includes ``plan_only_until_authorized`` so resume keeps the confirmation
    latch that gates mutating tools until the user explicitly authorizes.
    """
    plan = getattr(session, "task_plan", None)
    plan_only = bool(getattr(session, "plan_only_until_authorized", False))
    if not isinstance(plan, TaskPlan) or not plan.steps:
        if plan_only:
            return {"plan": [], "plan_only_until_authorized": True}
        return None
    payload = task_plan_to_payload(plan)
    payload["plan_only_until_authorized"] = plan_only
    return payload


def _last_task_plan_content(
    prior_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for record in reversed(prior_records):
        if record.get("type") != "custom_message":
            continue
        if record.get("custom_type") != TASK_PLAN_STATE_CUSTOM_TYPE:
            continue
        content = record.get("content")
        return content if isinstance(content, dict) else None
    return None


def should_persist_task_plan_state(
    snapshot: dict[str, Any] | None,
    *,
    prior_records: Sequence[Mapping[str, Any]],
) -> bool:
    """Whether flush should append ``snapshot`` as a ``task_plan_state`` record.

    Skip identical tips. A ``None`` snapshot is a tombstone only when the
    transcript already stored a plan — otherwise skip so sessions that never
    planned stay quiet.
    """
    last = _last_task_plan_content(prior_records)
    if snapshot is None:
        return last is not None
    return last != snapshot


def apply_task_plan_state(session: Any, payload: Any) -> None:
    """Rehydrate ``session.task_plan`` (and the plan-only latch) from a snapshot."""
    if not hasattr(session, "task_plan"):
        return
    if not isinstance(payload, dict) or not payload:
        session.task_plan = None
        if hasattr(session, "plan_only_until_authorized"):
            session.plan_only_until_authorized = False
        return
    restored = task_plan_from_payload(payload)
    session.task_plan = restored
    if hasattr(session, "plan_only_until_authorized"):
        # An explicitly empty plan retains a restriction while its steps are rebuilt.
        session.plan_only_until_authorized = bool(
            (restored is not None or payload.get("plan") == [])
            and payload.get("plan_only_until_authorized")
        )


__all__ = [
    "TASK_PLAN_STATE_CUSTOM_TYPE",
    "apply_task_plan_state",
    "should_persist_task_plan_state",
    "task_plan_state_snapshot",
]
