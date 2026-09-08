"""SessionGoal (+ CTA state) survives flush → restore."""

from __future__ import annotations

from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
from core.agent_harness.session.pending_offer import PendingIntegrationSetupOffer
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
    session_goal_is_active,
)
from core.agent_harness.session_goal.persist import (
    SESSION_GOAL_STATE_CUSTOM_TYPE,
    apply_session_goal_state,
    session_goal_from_payload,
    session_goal_state_snapshot,
    session_goal_to_payload,
)
from core.agent_harness.session_goal.run_until import goal_has_stalled


def test_session_goal_round_trips_through_payload() -> None:
    goal = SessionGoal(
        condition="finish checklist",
        max_outer_turns=4,
        status=SessionGoalStatus.ACTIVE,
        turns_used=2,
        step_count=3,
        checklist=("a", "b", "c"),
        completed=frozenset({0}),
        last_progress_turns_used=2,
        last_reason="checklist 1/3 done — next: b",
    )
    restored = session_goal_from_payload(session_goal_to_payload(goal))
    assert restored == goal


def test_paused_session_goal_round_trips_through_payload() -> None:
    goal = SessionGoal(
        condition="finish checklist",
        max_outer_turns=4,
        status=SessionGoalStatus.PAUSED,
        turns_used=2,
        host_owned=True,
        last_reason="paused by you",
    )
    restored = session_goal_from_payload(session_goal_to_payload(goal))
    assert restored == goal
    assert restored is not None
    assert restored.status == SessionGoalStatus.PAUSED


def test_legacy_payload_starts_the_stall_clock_at_restored_turns() -> None:
    restored = session_goal_from_payload(
        {
            "condition": "finish checklist",
            "max_outer_turns": 5,
            "status": SessionGoalStatus.ACTIVE,
            "turns_used": 3,
            "checklist": ["a", "b", "c"],
            "completed": [0],
        }
    )

    assert restored is not None
    assert restored.last_progress_turns_used == 3
    assert goal_has_stalled(restored) is False


def test_session_goal_state_snapshot_includes_cta_and_pending() -> None:
    session = SessionCore(store=InMemorySessionStore())
    attach_session_goal(
        session,
        SessionGoal(condition="keep going", max_outer_turns=3, checklist=("one", "two")),
    )
    session.offered_upgrade_ctas.add("cta:posthog_mcp")
    session.pending_integration_setup_offer = PendingIntegrationSetupOffer(service_id="posthog_mcp")

    snapshot = session_goal_state_snapshot(session)
    other = SessionCore(store=InMemorySessionStore())
    apply_session_goal_state(other, snapshot)

    assert session_goal_is_active(other)
    assert other.session_goal is not None
    assert other.session_goal.checklist == ("one", "two")
    assert other.offered_upgrade_ctas == {"cta:posthog_mcp"}
    assert isinstance(other.pending_integration_setup_offer, PendingIntegrationSetupOffer)
    assert other.pending_integration_setup_offer.service_id == "posthog_mcp"


def test_flush_persists_session_goal_state_and_restore_context_applies_it() -> None:
    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    attach_session_goal(
        session,
        SessionGoal(
            condition="three steps",
            max_outer_turns=5,
            checklist=("gather", "analyze", "report"),
            completed=frozenset({0}),
            turns_used=1,
        ),
    )
    session.offered_upgrade_ctas.add("cta:posthog_mcp")

    storage.flush(session)
    records = storage.read(session.session_id)
    assert any(
        rec.get("type") == "custom_message"
        and rec.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
        for rec in records
    )
    content = next(
        rec["content"]
        for rec in records
        if rec.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    )

    restored = SessionCore(store=InMemorySessionStore())
    SessionManager(store=InMemorySessionStore()).restore_context(
        restored,
        {
            "cli_agent_messages": [],
            "accumulated_context": {},
            "session_goal_state": content,
            "history": [],
        },
    )
    assert session_goal_is_active(restored)
    assert restored.session_goal is not None
    assert restored.session_goal.completed == frozenset({0})
    assert restored.offered_upgrade_ctas == {"cta:posthog_mcp"}


def test_flush_after_leaf_persists_paused_session_goal() -> None:
    """End-of-turn flush writes a leaf; a later ``/goal pause`` must still land.

    Gateway rebuilds from the last ``session_goal_state`` on the tip branch. If
    flush no-ops on a trailing leaf, pause stays in memory only and the next
    inbound turn resumes the pre-pause ACTIVE snapshot.
    """
    from core.agent_harness.session_goal.goal import SessionGoalReason, SessionGoalStatus

    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    attach_session_goal(
        session,
        SessionGoal(condition="ship the fix", max_outer_turns=4, host_owned=True),
    )
    storage.flush(session)
    assert storage.read(session.session_id)[-1].get("type") == "leaf"

    paused = session.session_goal
    assert paused is not None
    attach_session_goal(
        session,
        paused.with_status(SessionGoalStatus.PAUSED).with_reason(SessionGoalReason.PAUSED_BY_USER),
    )
    storage.flush(session)

    goal_records = [
        rec
        for rec in storage.read(session.session_id)
        if rec.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert len(goal_records) >= 2
    assert goal_records[-1]["content"]["session_goal"]["status"] == "paused"
    assert sum(1 for rec in storage.read(session.session_id) if rec.get("type") == "leaf") == 1


def test_clearing_a_goal_writes_a_tombstone_so_resume_does_not_revive_it() -> None:
    """An empty flush after a prior goal must clear resume, not suppress the write.

    Skipping empty snapshots avoids leaf noise for sessions that never had a
    goal. Once a non-empty ``session_goal_state`` exists, a later clear must
    append a tombstone or resume keeps the old goal / CTA state authoritative.
    """
    from core.agent_harness.session_goal.goal import clear_session_goal
    from core.agent_harness.session_goal.persist import session_goal_state_is_empty

    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    attach_session_goal(
        session,
        SessionGoal(condition="keep going", max_outer_turns=3, checklist=("one", "two")),
    )
    session.offered_upgrade_ctas.add("cta:posthog_mcp")
    storage.flush(session)

    clear_session_goal(session)
    session.offered_upgrade_ctas.clear()
    session.pending_integration_setup_offer = None
    # Mid-session flush must write a tombstone even when the tip is already a
    # trailing leaf (gateway ``/goal clear`` / pause after end-of-turn flush).
    storage.flush(session)

    goal_records = [
        record
        for record in storage.read(session.session_id)
        if record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert len(goal_records) >= 2
    assert session_goal_state_is_empty(goal_records[-1]["content"])

    restored = SessionCore(store=InMemorySessionStore())
    SessionManager(store=InMemorySessionStore()).restore_context(
        restored,
        {
            "cli_agent_messages": [],
            "accumulated_context": {},
            "session_goal_state": goal_records[-1]["content"],
            "history": [],
        },
    )
    assert not session_goal_is_active(restored)
    assert restored.session_goal is None
    assert restored.offered_upgrade_ctas == set()
    assert restored.pending_integration_setup_offer is None


def test_clearing_a_goal_is_persisted_so_resume_does_not_revive_it() -> None:
    """A cleared goal must not come back when the session is resumed.

    Empty goal snapshots are suppressed at flush so they cannot re-parent the
    transcript leaf. That suppression must not hide a *transition* to empty:
    without a tombstone the last persisted snapshot still holds the goal, and
    restore reattaches something the user explicitly cleared.
    """
    # Arrange: a session that had a goal, flushed once.
    from core.agent_harness.session.persistence.memory import InMemorySessionStore
    from core.agent_harness.session.session_core import SessionCore
    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        attach_session_goal,
        clear_session_goal,
    )
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE

    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    session.record("cli_agent", "start", ok=True)
    attach_session_goal(session, SessionGoal(condition="finish it", max_outer_turns=3))
    storage.flush(session)

    # Act: the user runs ``/goal clear`` — dispatch_slash records the row.
    session.record("slash", "/goal clear", ok=True)
    clear_session_goal(session)
    storage.flush(session)

    # Assert: the last persisted snapshot carries no goal.
    snapshots = [
        record["content"]
        for record in storage.read(session.session_id)
        if record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert len(snapshots) == 2, "the clear was not tombstoned"
    assert snapshots[-1].get("session_goal") is None


def test_the_last_verdict_survives_a_round_trip() -> None:
    from core.agent_harness.session_goal.goal import SessionGoal
    from core.agent_harness.session_goal.persist import (
        session_goal_from_payload,
        session_goal_to_payload,
    )

    goal = SessionGoal(condition="c", last_verdict="Contradiction: 5 vs 3")

    restored = session_goal_from_payload(session_goal_to_payload(goal))

    assert restored is not None
    assert restored.last_verdict == "Contradiction: 5 vs 3"
