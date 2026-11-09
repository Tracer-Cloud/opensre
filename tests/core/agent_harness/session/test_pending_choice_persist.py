"""Pending Ask User choices survive a headless process boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.session import (
    JsonlSessionRepo,
    JsonlSessionStore,
    SessionCore,
    SessionManager,
)
from core.agent_harness.session.pending_choice import AskUserQuestion, PendingUserChoice
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.session.persistence.paths import session_path
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _pending_choice() -> PendingUserChoice:
    return PendingUserChoice(
        title="Ask User",
        options=("Tracer-Cloud/opensre", "Another repository"),
        questions=(
            AskUserQuestion(
                label="Repository",
                title="Which repository should I inspect?",
                options=("Tracer-Cloud/opensre", "Another repository"),
            ),
        ),
        note="The repository determines which checks are inspected.",
    )


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)


def test_flush_and_restore_preserve_pending_choice_and_skill() -> None:
    storage = JsonlSessionStore()
    repo = JsonlSessionRepo()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "investigate CI")
    session.pending_user_choice = _pending_choice()
    session.active_skill = "reporting-github-ci-failures"
    session.questions_already_answered.add("which signal?")

    storage.flush(session)
    data = repo.load_session(session.session_id)
    assert data is not None

    restored = SessionCore(store=storage)
    SessionManager(store=storage, repo=repo).restore_context(restored, data)

    assert restored.pending_user_choice == session.pending_user_choice
    assert restored.active_skill == "reporting-github-ci-failures"
    assert restored.questions_already_answered == {"which signal?"}


def test_clearing_pending_choice_writes_a_tombstone() -> None:
    storage = JsonlSessionStore()
    repo = JsonlSessionRepo()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "investigate CI")
    session.pending_user_choice = _pending_choice()
    storage.flush(session)

    session.pending_user_choice = None
    session.active_skill = None
    storage.flush(session)
    data = repo.load_session(session.session_id)
    assert data is not None

    restored = SessionCore(store=storage)
    SessionManager(store=storage, repo=repo).restore_context(restored, data)

    assert restored.pending_user_choice is None
    assert restored.active_skill is None

    storage.flush(session)
    records = [
        json.loads(line) for line in session_path(session.session_id).read_text().splitlines()
    ]
    tombstones = [
        record
        for record in records
        if record.get("custom_type") == "pending_user_choice_state" and record.get("content") == {}
    ]
    assert len(tombstones) == 1


def test_consuming_pending_choice_preserves_settled_workflow_context() -> None:
    storage = JsonlSessionStore()
    repo = JsonlSessionRepo()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "investigate CI")
    session.pending_user_choice = _pending_choice()
    session.active_skill = "reporting-github-ci-failures"
    session.ask_user_rounds = 1
    session.questions_already_answered.add("earlier question")
    session.skill_question_keys[session.active_skill] = {"earlier question", "settled question"}
    storage.flush(session)

    session.pending_user_choice = None
    session.questions_already_answered.add("settled question")
    storage.flush(session)
    data = repo.load_session(session.session_id)
    assert data is not None

    restored = SessionCore(store=storage)
    SessionManager(store=storage, repo=repo).restore_context(restored, data)

    assert restored.pending_user_choice is None
    assert restored.active_skill == "reporting-github-ci-failures"
    assert restored.ask_user_rounds == 1
    assert restored.questions_already_answered == {"earlier question", "settled question"}
    assert restored.skill_question_keys == {
        "reporting-github-ci-failures": {"earlier question", "settled question"}
    }


def test_flush_keeps_silent_pending_choice_resumable() -> None:
    storage = JsonlSessionStore()
    repo = JsonlSessionRepo()
    session = SessionCore(store=storage)
    storage.open_session(session)
    session.pending_user_choice = _pending_choice()

    storage.flush(session)
    data = repo.load_session(session.session_id)
    assert data is not None

    restored = SessionCore(store=storage)
    SessionManager(store=storage, repo=repo).restore_context(restored, data)

    assert restored.pending_user_choice == session.pending_user_choice


def test_memory_store_keeps_silent_pending_choice() -> None:
    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    session.pending_user_choice = _pending_choice()

    storage.flush(session)

    assert any(
        record.get("custom_type") == "pending_user_choice_state"
        for record in storage.read(session.session_id)
    )


def test_silent_pending_choice_persists_its_initiating_prompt() -> None:
    storage = JsonlSessionStore()
    repo = JsonlSessionRepo()
    session = SessionCore(store=storage)
    storage.open_session(session)
    session.pending_user_choice = _pending_choice()
    result = TurnResult(
        final_intent="answer",
        action_result=ToolCallingTurnResult(0, 0, 0, False, False),
    )

    DefaultTurnAccounting(session, "deploy the app").finalize(result)
    storage.flush(session)
    data = repo.load_session(session.session_id)

    assert data is not None
    assert ("user", "deploy the app") in data["cli_agent_messages"]
