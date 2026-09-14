"""Pending Ask User choices survive a headless process boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_harness.session import (
    JsonlSessionRepo,
    JsonlSessionStore,
    SessionCore,
    SessionManager,
)
from core.agent_harness.session.pending_choice import AskUserQuestion, PendingUserChoice


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

    storage.flush(session)
    data = repo.load_session(session.session_id)
    assert data is not None

    restored = SessionCore(store=storage)
    SessionManager(store=storage, repo=repo).restore_context(restored, data)

    assert restored.pending_user_choice == session.pending_user_choice
    assert restored.active_skill == "reporting-github-ci-failures"


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
