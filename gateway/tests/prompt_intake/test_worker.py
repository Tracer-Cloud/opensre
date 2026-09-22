"""Tests for the prompt worker: what a remote turn may do and how it settles."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from core.agent_harness import SessionCore, SessionManager
from core.agent_harness.session import InMemorySessionStore
from core.agent_harness.session.pending_choice import PendingUserChoice
from gateway.core.prompt_intake import (
    ERROR_CREDITS_DENIED,
    ERROR_NOT_ADMITTED,
    ERROR_TURN_FAILED,
    PromptQueue,
    PromptState,
    PromptWorker,
)
from infrastructure.turn_host.unattended_session import UnattendedSessions, restrict_to_unattended

_LOGGER = logging.getLogger("test")


class _Handler:
    """A fake turn callback that records what the worker gave it."""

    def __init__(self, *, answer: str = "", asks: PendingUserChoice | None = None) -> None:
        self.answer = answer
        self.asks = asks
        self.seen_text = ""
        self.seen_capabilities: dict[str, tuple[str, ...]] = {}
        self.result: Any = object()

    def run(self, text: str, session: SessionCore, output: Any, _logger: Any) -> Any:
        self.seen_text = text
        self.seen_capabilities = dict(session.available_capabilities)
        if self.asks is not None:
            session.pending_user_choice = self.asks
        elif self.result is None:
            return None
        else:
            output.finalize(self.answer)
        return self.result


def _worker(handler: _Handler) -> tuple[PromptWorker, PromptQueue]:
    queue = PromptQueue()
    sessions = UnattendedSessions(SessionManager(store=InMemorySessionStore()))
    worker = PromptWorker(queue, handler, logger=_LOGGER, sessions=sessions)
    return worker, queue


@pytest.fixture(autouse=True)
def _no_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORGANIZATION_ID", raising=False)


def test_a_remote_turn_cannot_ask_or_switch_runtime_and_gets_the_context_as_facts() -> None:
    # Arrange
    handler = _Handler(answer="3 scheduled tasks are running.")
    worker, queue = _worker(handler)
    job = queue.submit(
        "Which scheduled tasks run on this gateway?",
        context={"repository": "Tracer-Cloud/opensre"},
        actor="user_1",
    )
    assert job is not None

    # Act
    worker.run_one(job)

    # Assert
    assert job.state is PromptState.DONE and job.answer == "3 scheduled tasks are running."
    assert handler.seen_capabilities["ask_user_choice"] == ("deferred",)
    assert handler.seen_capabilities["slash_commands"] == ()
    assert handler.seen_capabilities["llm_provider"] == ()
    assert handler.seen_capabilities["cli_commands"] == ()
    assert handler.seen_capabilities["hosted_gateway"] == ()
    assert handler.seen_text.endswith("Known context:\n- repository: Tracer-Cloud/opensre")


def test_a_question_ends_the_turn_as_needs_input_with_the_question_as_text() -> None:
    # Arrange
    pending = PendingUserChoice(title="Which branch?", options=("main", "release"))
    worker, queue = _worker(_Handler(asks=pending))
    job = queue.submit("fix ci", context={}, actor="user_1")
    assert job is not None

    # Act
    worker.run_one(job)

    # Assert
    assert job.state is PromptState.NEEDS_INPUT
    assert job.question == "Which branch?\nOptions: main, release"


def test_a_rejected_admission_and_a_failed_turn_become_stable_codes() -> None:
    # Arrange
    not_admitted = _Handler()
    not_admitted.result = None

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("provider exploded: secret detail")

    worker_a, queue_a = _worker(not_admitted)
    exploding = _Handler()
    exploding.run = explode  # type: ignore[method-assign]
    worker_b, queue_b = _worker(exploding)
    job_a = queue_a.submit("a", context={}, actor="u")
    job_b = queue_b.submit("b", context={}, actor="u")
    assert job_a is not None and job_b is not None

    # Act
    worker_a.run_one(job_a)
    worker_b.run_one(job_b)

    # Assert: codes only, no exception text reaches the caller's view.
    assert job_a.view()["error"] == ERROR_NOT_ADMITTED
    assert job_b.view()["error"] == ERROR_TURN_FAILED
    assert "secret detail" not in str(job_b.view())
    assert ERROR_CREDITS_DENIED == "credits_denied"


def test_restriction_leaves_other_capabilities_alone() -> None:
    # Arrange
    session = SessionCore()
    session.available_capabilities["something_else"] = ("on",)

    # Act
    restrict_to_unattended(session)

    # Assert
    assert session.available_capabilities["something_else"] == ("on",)
