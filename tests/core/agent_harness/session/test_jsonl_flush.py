"""Characterization + cost tests for ``JsonlSessionStore.flush``.

``flush`` closes a session by appending a ``leaf`` record carrying the turn
counts. It reached those counts by re-parsing the whole session file after each
intervening append, so a long session paid three full ``read_text`` + per-line
``json.loads`` passes at the moment the user is waiting to exit.

The counts pinned here are the observable contract; the read count below is the
cost of producing them. The first test must pass before and after any change to
how the records are gathered.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_harness.session.persistence.contracts import RestoreContextKey
from core.agent_harness.session.persistence.jsonl_repo import JsonlSessionRepo
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session.persistence.paths import session_path


@pytest.fixture
def storage_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point session storage at a temp home so nothing touches ~/.opensre."""
    monkeypatch.setenv("OPENSRE_HOME", str(tmp_path))
    from config.constants import paths as paths_constants

    monkeypatch.setattr(paths_constants, "OPENSRE_HOME_DIR", tmp_path, raising=False)
    return tmp_path


def _session(session_id: str = "sess-flush", **overrides: Any) -> Any:
    """A minimal object satisfying ``SessionPersistenceSource``."""
    return SimpleNamespace(
        session_id=session_id,
        started_at=0.0,
        agent=SimpleNamespace(messages=overrides.pop("messages", [])),
        accumulated_context=overrides.pop("accumulated_context", {}),
    )


def _seed_turns(storage: JsonlSessionStore, session: Any) -> None:
    """Two chat turns and one alert turn, written the way the runtime writes them.

    ``append_turn`` puts ``kind`` at the record top level, which is where the
    counters read it — a stub built through ``append_custom_message`` would nest
    it under ``content`` and silently count zero.
    """
    storage.append_turn(session, "chat", "one")
    storage.append_turn(session, "chat", "two")
    storage.append_turn(session, "alert", "three")


def _leaf(session_id: str) -> dict[str, Any]:
    lines = session_path(session_id).read_text(encoding="utf-8").splitlines()
    leaves = [json.loads(line) for line in lines if json.loads(line).get("type") == "leaf"]
    assert len(leaves) == 1, f"expected exactly one leaf, got {len(leaves)}"
    return leaves[0]


def test_flush_leaf_counts_survive_context_and_message_appends(storage_home: Path) -> None:
    """The leaf's counts are the contract; the appends before it must not shift them.

    ``flush`` appends an accumulated-context record and the chat messages before
    counting. Neither is a ``turn_stub``, so both counts have to reflect the
    turns seeded here and nothing else.
    """
    # Arrange
    storage = JsonlSessionStore()
    session = _session(
        accumulated_context={"cluster": "prod"},
        messages=[("user", "hello"), ("assistant", "hi")],
    )
    storage.open_session(session)
    _seed_turns(storage, session)

    # Act
    storage.flush(session)

    # Assert
    leaf = _leaf(session.session_id)
    assert leaf["total_turns"] == 3
    assert leaf["chat_turns"] == 2


def test_flush_still_persists_context_and_messages(storage_home: Path) -> None:
    """Both appends must still reach the file, whatever flush does with records."""
    # Arrange
    storage = JsonlSessionStore()
    session = _session(
        accumulated_context={"cluster": "prod"},
        messages=[("user", "hello")],
    )
    storage.open_session(session)
    _seed_turns(storage, session)

    # Act
    storage.flush(session)

    # Assert
    records = [
        json.loads(line)
        for line in session_path(session.session_id).read_text(encoding="utf-8").splitlines()
    ]
    assert any(rec.get("custom_type") == "accumulated_context" for rec in records)
    assert any(rec.get("type") == "message" and rec["role"] == "user" for rec in records)


def test_goal_control_state_flush_does_not_finalize_the_active_turn(storage_home: Path) -> None:
    from core.agent_harness.session_goal.goal import SessionGoal
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE

    storage = JsonlSessionStore()
    session = _session()
    session.session_goal = SessionGoal(condition="finish safely", max_outer_turns=3)
    session.offered_upgrade_ctas = set()
    session.pending_integration_setup_offer = None
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")

    storage.flush_session_goal_control_state(session)
    session.session_goal = None
    storage.flush_session_goal_control_state(session)

    records = [
        json.loads(line)
        for line in session_path(session.session_id).read_text(encoding="utf-8").splitlines()
    ]
    goal_states = [
        record for record in records if record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert goal_states[-1]["content"]["session_goal"] is None
    assert not any(record.get("type") == "leaf" for record in records)


def test_goal_control_snapshot_keeps_goal_and_plan_consistent_after_a_write_failure(
    storage_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_harness.session_goal.goal import SessionGoal
    from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

    storage = JsonlSessionStore()
    session = _session()
    session.session_goal = SessionGoal(condition="finish safely", max_outer_turns=3)
    session.task_plan = TaskPlan(
        steps=(PlanStep(step="finish safely", status=PlanStepStatus.IN_PROGRESS),)
    )
    session.offered_upgrade_ctas = set()
    session.pending_integration_setup_offer = None
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    storage.flush(session)
    session.session_goal = None
    session.task_plan = None

    def _fail_goal_state_write(*_args: object) -> bool:
        return False

    monkeypatch.setattr(storage, "_append_session_goal_state", _fail_goal_state_write)
    with pytest.raises(OSError, match="session-goal state"):
        storage.flush_session_goal_control_state(session)

    restored = JsonlSessionRepo().load_session(session.session_id)

    assert restored is not None
    assert restored[RestoreContextKey.SESSION_GOAL_STATE]["session_goal"] is None
    assert restored[RestoreContextKey.TASK_PLAN_STATE] == {}


def test_goal_control_sidecar_is_durable_and_acknowledged(storage_home: Path) -> None:
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    records = [
        json.loads(line)
        for line in session_path(session.session_id).read_text(encoding="utf-8").splitlines()
    ]
    historical_entry_id = str(records[-1]["id"])
    storage.append_turn(session, "chat", "continue")
    records = [
        json.loads(line)
        for line in session_path(session.session_id).read_text(encoding="utf-8").splitlines()
    ]
    control_target_entry_id = str(records[-1]["id"])

    control_id = storage.append_session_goal_control(session.session_id, "goal_clear")

    loaded = JsonlSessionRepo().load_session(session.session_id)
    assert loaded is not None
    assert loaded[RestoreContextKey.SESSION_GOAL_CONTROLS] == [
        {"control_id": control_id, "reason": "goal_clear"}
    ]
    historical = JsonlSessionRepo().load_session(f"{session.session_id}:{historical_entry_id[:8]}")
    assert historical is not None
    assert historical[RestoreContextKey.SESSION_GOAL_CONTROLS] == []
    target_snapshot = JsonlSessionRepo().load_session(
        f"{session.session_id}:{control_target_entry_id[:8]}"
    )
    assert target_snapshot is not None
    assert target_snapshot[RestoreContextKey.SESSION_GOAL_CONTROLS] == []
    storage.append_message(
        session.session_id,
        role="user",
        content="alternate branch",
        parent_id=historical_entry_id,
    )
    alternate = JsonlSessionRepo().load_session(session.session_id)
    assert alternate is not None
    assert alternate[RestoreContextKey.SESSION_GOAL_CONTROLS] == []
    storage.complete_session_goal_control(session.session_id, control_id)
    reloaded = JsonlSessionRepo().load_session(session.session_id)
    assert reloaded is not None
    assert reloaded[RestoreContextKey.SESSION_GOAL_CONTROLS] == []


def test_goal_control_sidecar_does_not_reopen_a_closed_tip(storage_home: Path) -> None:
    storage = JsonlSessionStore()
    session = _session()
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    storage.flush(session)
    storage.append_session_goal_control(session.session_id, "goal_clear")

    storage.flush(session)

    records = [
        json.loads(line)
        for line in session_path(session.session_id).read_text(encoding="utf-8").splitlines()
    ]
    assert sum(record.get("type") == "leaf" for record in records) == 1


def test_goal_control_write_failure_is_not_suppressed(storage_home: Path) -> None:
    with pytest.raises(OSError, match="Could not persist session-goal control"):
        JsonlSessionStore().append_session_goal_control("missing-session", "goal_clear")


def test_flush_parses_the_session_file_once(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing a session must not re-parse the whole transcript per append.

    Both intervening appends write records the counts ignore — an
    ``accumulated_context`` record and plain ``message`` records, neither a
    ``turn_stub`` — so re-reading the file after each one bought nothing and
    cost two extra full passes on the exit path.
    """
    # Arrange
    storage = JsonlSessionStore()
    session = _session(
        accumulated_context={"cluster": "prod"},
        messages=[("user", "hello")],
    )
    storage.open_session(session)
    _seed_turns(storage, session)

    reads: list[str] = []
    real_read_records = JsonlSessionStore._read_records

    def _counting_read(path: Path) -> list[dict[str, Any]]:
        reads.append(str(path))
        return real_read_records(path)

    monkeypatch.setattr(JsonlSessionStore, "_read_records", staticmethod(_counting_read))

    # Act
    storage.flush(session)

    # Assert
    assert len(reads) == 1, f"flush parsed the session file {len(reads)} times"
