"""Persist / restore SessionGoal (+ L0 CTA) flush state.

Leaf module: imports :mod:`core.agent_harness.session_goal.goal` only —
do not import this from ``goal`` (avoids ``py/cyclic-import``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.agent_harness.session_goal.goal import SessionGoal, SessionGoalStatus

# Persisted on flush as ``custom_message`` / ``custom_type`` (last write wins).
SESSION_GOAL_STATE_CUSTOM_TYPE = "session_goal_state"


def session_goal_to_payload(goal: SessionGoal) -> dict[str, Any]:
    """JSON-ready dict for persistence / host transport."""
    payload: dict[str, Any] = {
        "condition": goal.condition,
        "max_outer_turns": int(goal.max_outer_turns),
        "status": goal.status,
        "turns_used": int(goal.turns_used),
        "step_count": goal.step_count,
        "checklist": list(goal.checklist),
        "completed": sorted(int(index) for index in goal.completed),
        "last_reason": goal.last_reason,
        "token_baseline_input": int(goal.token_baseline_input),
        "token_baseline_output": int(goal.token_baseline_output),
        "host_owned": bool(goal.host_owned),
        "last_progress_turns_used": int(goal.last_progress_turns_used),
    }
    if goal.started_at is not None:
        payload["started_at"] = float(goal.started_at)
    return payload


def session_goal_from_payload(payload: Any) -> SessionGoal | None:
    """Rebuild a :class:`SessionGoal` from :func:`session_goal_to_payload` output."""
    if not isinstance(payload, dict):
        return None
    condition = payload.get("condition")
    if not isinstance(condition, str) or not condition.strip():
        return None
    try:
        max_outer = max(1, int(payload.get("max_outer_turns", 5)))
        turns_used = max(0, int(payload.get("turns_used", 0)))
    except (TypeError, ValueError):
        return None
    step_raw = payload.get("step_count")
    step_count: int | None
    if step_raw is None:
        step_count = None
    else:
        try:
            step_count = max(1, int(step_raw))
        except (TypeError, ValueError):
            step_count = None
    checklist_raw = payload.get("checklist") or ()
    checklist = tuple(
        item.strip() for item in checklist_raw if isinstance(item, str) and item.strip()
    )
    completed_raw = payload.get("completed") or ()
    completed: set[int] = set()
    if isinstance(completed_raw, (list, tuple, set, frozenset)):
        for value in completed_raw:
            try:
                completed.add(int(value))
            except (TypeError, ValueError):
                continue
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        status = SessionGoalStatus.ACTIVE
    reason_raw = payload.get("last_reason")
    last_reason = reason_raw.strip() if isinstance(reason_raw, str) else ""
    started_at: float | None = None
    started_raw = payload.get("started_at")
    if started_raw is not None:
        try:
            started_at = float(started_raw)
        except (TypeError, ValueError):
            started_at = None
    try:
        token_in = max(0, int(payload.get("token_baseline_input", 0) or 0))
        token_out = max(0, int(payload.get("token_baseline_output", 0) or 0))
    except (TypeError, ValueError):
        token_in, token_out = 0, 0
    host_owned = bool(payload.get("host_owned", False))
    last_progress_turns_used = _restore_last_progress_turns_used(payload, turns_used)
    return SessionGoal(
        condition=condition.strip(),
        max_outer_turns=max_outer,
        status=status.strip(),
        turns_used=turns_used,
        step_count=step_count,
        checklist=checklist,
        completed=frozenset(completed),
        last_reason=last_reason,
        started_at=started_at,
        token_baseline_input=token_in,
        token_baseline_output=token_out,
        host_owned=host_owned,
        last_progress_turns_used=last_progress_turns_used,
    )


def _restore_last_progress_turns_used(payload: dict[str, Any], turns_used: int) -> int:
    """Stall watermark, or ``turns_used`` when the key is missing or unreadable."""
    if "last_progress_turns_used" not in payload:
        return turns_used
    raw = payload.get("last_progress_turns_used")
    if raw is None:
        return turns_used
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return turns_used


def session_goal_state_snapshot(session: Any) -> dict[str, Any]:
    """Flush payload for session goal + L0 CTA dedupe / pending setup offer."""
    goal = getattr(session, "session_goal", None)
    offered = getattr(session, "offered_upgrade_ctas", None)
    offered_keys = (
        sorted(str(key) for key in offered)
        if isinstance(offered, (set, frozenset, list, tuple))
        else []
    )
    pending = getattr(session, "pending_integration_setup_offer", None)
    service_id = getattr(pending, "service_id", None)
    pending_payload = (
        {"service_id": service_id.strip()}
        if isinstance(service_id, str) and service_id.strip()
        else None
    )
    return {
        "session_goal": (session_goal_to_payload(goal) if isinstance(goal, SessionGoal) else None),
        "offered_upgrade_ctas": offered_keys,
        "pending_integration_setup_offer": pending_payload,
    }


def session_goal_state_is_empty(snapshot: dict[str, Any]) -> bool:
    """True when the snapshot carries no goal, no offered CTA, and no pending offer."""
    return not any(snapshot.values())


def _last_session_goal_state_content(
    prior_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for record in reversed(prior_records):
        if record.get("type") != "custom_message":
            continue
        if record.get("custom_type") != SESSION_GOAL_STATE_CUSTOM_TYPE:
            continue
        content = record.get("content")
        return content if isinstance(content, dict) else None
    return None


def should_persist_session_goal_state(
    snapshot: dict[str, Any],
    *,
    prior_records: Sequence[Mapping[str, Any]],
) -> bool:
    """Whether flush should append ``snapshot`` as a ``session_goal_state`` record.

    Skip when the tip already carries an identical snapshot (keeps mid-session
    flush after a trailing ``leaf`` idempotent). Non-empty state always
    persists when it differs. An empty snapshot is skipped only when the
    transcript has never stored goal/CTA state — otherwise it is a tombstone so
    resume does not revive a cleared goal.
    """
    last = _last_session_goal_state_content(prior_records)
    if last == snapshot:
        return False
    if not session_goal_state_is_empty(snapshot):
        return True
    return last is not None


def apply_session_goal_state(session: Any, payload: Any) -> None:
    """Rehydrate session goal / CTA state from a flush snapshot."""
    if not isinstance(payload, dict):
        return
    goal = session_goal_from_payload(payload.get("session_goal"))
    if hasattr(session, "session_goal"):
        session.session_goal = goal
    offered_raw = payload.get("offered_upgrade_ctas") or ()
    if hasattr(session, "offered_upgrade_ctas"):
        keys = {str(key) for key in offered_raw if isinstance(key, str) and key.strip()}
        session.offered_upgrade_ctas = keys
    pending_raw = payload.get("pending_integration_setup_offer")
    if hasattr(session, "pending_integration_setup_offer"):
        service_id = None
        if isinstance(pending_raw, dict):
            raw = pending_raw.get("service_id")
            if isinstance(raw, str) and raw.strip():
                service_id = raw.strip()
        if service_id is None:
            session.pending_integration_setup_offer = None
        else:
            from core.agent_harness.session.pending_offer import (
                PendingIntegrationSetupOffer,
            )

            session.pending_integration_setup_offer = PendingIntegrationSetupOffer(
                service_id=service_id
            )


__all__ = [
    "SESSION_GOAL_STATE_CUSTOM_TYPE",
    "apply_session_goal_state",
    "session_goal_from_payload",
    "session_goal_state_is_empty",
    "session_goal_state_snapshot",
    "session_goal_to_payload",
    "should_persist_session_goal_state",
]
