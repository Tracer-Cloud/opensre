"""TaskPlan survives flush → restore so compacted transcripts keep the checklist."""

from __future__ import annotations

from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
from core.agent_harness.task_plan.persist import (
    TASK_PLAN_STATE_CUSTOM_TYPE,
    apply_task_plan_state,
    should_persist_task_plan_state,
    task_plan_state_snapshot,
)
from core.agent_harness.task_plan.plan import parse_task_plan, task_plan_to_payload


def _plan():
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Run /health and read the result", "status": "completed"},
                {"step": "List connected integrations", "status": "in_progress"},
                {"step": "Confirm both outputs answered the ask", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    return plan


def test_flush_persists_task_plan_and_restore_context_applies_it() -> None:
    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    session.task_plan = _plan()
    session.plan_only_until_authorized = True

    storage.flush(session)
    records = storage.read(session.session_id)
    content = next(
        rec["content"] for rec in records if rec.get("custom_type") == TASK_PLAN_STATE_CUSTOM_TYPE
    )
    assert content["plan_only_until_authorized"] is True

    restored = SessionCore(store=InMemorySessionStore())
    SessionManager(store=InMemorySessionStore()).restore_context(
        restored,
        {
            "cli_agent_messages": [],
            "accumulated_context": {},
            "task_plan_state": content,
            "history": [],
        },
    )
    assert restored.task_plan is not None
    assert restored.task_plan.current_index == 2
    assert restored.task_plan.steps[-1].step.startswith("Confirm")
    assert restored.plan_only_until_authorized is True


def test_clearing_the_plan_writes_a_tombstone() -> None:
    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    session.task_plan = _plan()
    session.plan_only_until_authorized = True
    storage.flush(session)

    session.task_plan = None
    session.plan_only_until_authorized = False
    storage.flush(session)

    snapshots = [
        record["content"]
        for record in storage.read(session.session_id)
        if record.get("custom_type") == TASK_PLAN_STATE_CUSTOM_TYPE
    ]
    assert len(snapshots) >= 2
    restored = SessionCore(store=InMemorySessionStore())
    apply_task_plan_state(restored, snapshots[-1])
    assert restored.task_plan is None
    assert restored.plan_only_until_authorized is False
    assert task_plan_state_snapshot(restored) is None


def test_apply_task_plan_state_restores_plan_only_latch() -> None:
    session = SessionCore(store=InMemorySessionStore())
    session.task_plan = _plan()
    session.plan_only_until_authorized = True
    payload = task_plan_state_snapshot(session)
    assert payload is not None
    assert payload["plan_only_until_authorized"] is True

    restored = SessionCore(store=InMemorySessionStore())
    apply_task_plan_state(restored, payload)
    assert restored.plan_only_until_authorized is True
    assert restored.task_plan is not None
    assert restored.task_plan.current_index == 2


def test_apply_task_plan_state_restores_disarmed_latch() -> None:
    session = SessionCore(store=InMemorySessionStore())
    session.task_plan = _plan()
    payload = task_plan_state_snapshot(session)
    assert payload is not None
    assert payload["plan_only_until_authorized"] is False

    restored = SessionCore(store=InMemorySessionStore())
    restored.plan_only_until_authorized = True
    apply_task_plan_state(restored, payload)
    assert restored.plan_only_until_authorized is False


def test_should_not_persist_identical_snapshot() -> None:
    session = SessionCore(store=InMemorySessionStore())
    session.task_plan = _plan()
    snapshot = task_plan_state_snapshot(session)
    assert snapshot is not None
    assert not should_persist_task_plan_state(
        snapshot,
        prior_records=[
            {
                "type": "custom_message",
                "custom_type": TASK_PLAN_STATE_CUSTOM_TYPE,
                "content": snapshot,
            }
        ],
    )


def test_should_not_tombstone_a_session_that_never_planned() -> None:
    assert not should_persist_task_plan_state(None, prior_records=[])


def test_apply_non_dict_payload_is_a_tombstone() -> None:
    session = SessionCore(store=InMemorySessionStore())
    session.task_plan = _plan()
    session.plan_only_until_authorized = True
    apply_task_plan_state(session, ["not", "a", "dict"])
    assert session.task_plan is None
    assert session.plan_only_until_authorized is False


def test_legacy_snapshot_without_latch_key_does_not_arm_authorization() -> None:
    session = SessionCore(store=InMemorySessionStore())
    session.plan_only_until_authorized = True
    apply_task_plan_state(session, task_plan_to_payload(_plan()))
    assert session.task_plan is not None
    assert session.plan_only_until_authorized is False


def test_corrupt_snapshot_does_not_arm_authorization() -> None:
    session = SessionCore(store=InMemorySessionStore())
    session.plan_only_until_authorized = True
    apply_task_plan_state(session, {"plan_only_until_authorized": True})
    assert session.task_plan is None
    assert session.plan_only_until_authorized is False


def test_latch_change_without_plan_change_is_persisted() -> None:
    session = SessionCore(store=InMemorySessionStore())
    session.task_plan = _plan()
    unarmed = task_plan_state_snapshot(session)
    session.plan_only_until_authorized = True
    armed = task_plan_state_snapshot(session)
    assert unarmed is not None and armed is not None
    assert should_persist_task_plan_state(
        armed,
        prior_records=[
            {
                "type": "custom_message",
                "custom_type": TASK_PLAN_STATE_CUSTOM_TYPE,
                "content": unarmed,
            }
        ],
    )
