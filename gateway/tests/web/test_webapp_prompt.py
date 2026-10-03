"""Tests for ``POST /v1/prompt`` and ``GET /v1/prompt/{id}``: who may call, what is accepted."""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.constants.gateway import PROMPT_MAX_CHARS
from gateway.core.prompt_intake import JsonlPromptJobStore, PromptQueue, PromptState
from gateway.web import webapp

_LOOPBACK = ("127.0.0.1", 40000)
_REMOTE = ("203.0.113.9", 40000)


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[PromptQueue]:
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    attached = PromptQueue(max_queued=1)
    webapp.app.state.prompt_queue = attached
    yield attached
    del webapp.app.state.prompt_queue


def test_a_prompt_is_queued_and_its_result_can_be_read_back(queue: PromptQueue) -> None:
    # Arrange
    client = TestClient(webapp.app, client=_LOOPBACK)

    # Act
    submitted = client.post(
        "/v1/prompt", json={"prompt": "  which tasks run?  ", "context": {"repo": "o/r"}}
    )
    fetched = client.get(f"/v1/prompt/{submitted.json()['prompt_id']}")
    unknown = client.get("/v1/prompt/p_nope")

    # Assert
    assert submitted.status_code == HTTPStatus.ACCEPTED and submitted.json()["state"] == "queued"
    assert fetched.status_code == HTTPStatus.OK and fetched.json()["state"] == "queued"
    assert unknown.status_code == HTTPStatus.NOT_FOUND
    job = queue.get(submitted.json()["prompt_id"])
    assert job is not None and job.prompt == "which tasks run?" and job.context == {"repo": "o/r"}


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"context": {}}, HTTPStatus.BAD_REQUEST, "prompt_required"),
        (
            {"prompt": "x" * (PROMPT_MAX_CHARS + 1)},
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "prompt_too_large",
        ),
        ({"prompt": "ok", "context": {"k": 1}}, HTTPStatus.BAD_REQUEST, "invalid_context"),
        ({"prompt": "ok", "actor": ""}, HTTPStatus.BAD_REQUEST, "invalid_actor"),
    ],
)
def test_bad_bodies_are_refused_with_a_code(
    queue: PromptQueue, body: dict[str, object], status: HTTPStatus, code: str
) -> None:
    # Arrange
    client = TestClient(webapp.app, client=_LOOPBACK)

    # Act
    response = client.post("/v1/prompt", json=body)

    # Assert
    assert (response.status_code, response.json()["error"]) == (status, code)
    assert queue.queued_count() == 0


def test_a_full_queue_answers_too_many_prompts(queue: PromptQueue) -> None:
    # Arrange
    client = TestClient(webapp.app, client=_LOOPBACK)
    client.post("/v1/prompt", json={"prompt": "first"})
    assert queue.queued_count() == 1

    # Act
    response = client.post("/v1/prompt", json={"prompt": "second"})

    # Assert
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["error"] == "too_many_prompts"


@pytest.mark.usefixtures("queue")
def test_a_remote_caller_needs_the_listener_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("OPENSRE_ALERT_LISTENER_TOKEN", "sekret")
    client = TestClient(webapp.app, client=_REMOTE)

    # Act
    without = client.post("/v1/prompt", json={"prompt": "hi"})
    wrong = client.post(
        "/v1/prompt", json={"prompt": "hi"}, headers={"Authorization": "Bearer nope"}
    )
    right = client.post(
        "/v1/prompt", json={"prompt": "hi"}, headers={"Authorization": "Bearer sekret"}
    )

    # Assert
    assert without.status_code == HTTPStatus.UNAUTHORIZED
    assert wrong.status_code == HTTPStatus.UNAUTHORIZED
    assert right.status_code == HTTPStatus.ACCEPTED


def test_without_a_gateway_the_route_says_so_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the interactive shell serves this app without a prompt queue
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    client = TestClient(webapp.app, client=_LOOPBACK)

    # Act
    response = client.post("/v1/prompt", json={"prompt": "hi"})

    # Assert
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["error"] == "prompt_intake_unavailable"


def test_an_answer_is_queued_as_a_follow_up_only_while_the_prompt_is_asking(
    queue: PromptQueue,
) -> None:
    # Arrange: one prompt waiting for an answer, one that is not
    client = TestClient(webapp.app, client=_LOOPBACK)
    asked = queue.submit("fix ci", context={}, actor="u")
    queue.take(timeout_seconds=0.01)
    done = queue.submit("other", context={}, actor="u")
    queue.take(timeout_seconds=0.01)
    assert asked is not None and done is not None
    queue.needs_input(asked, "Which branch?", choice={"title": "Which branch?"})
    queue.finish(done, "fine")

    # Act
    answered = client.post(f"/v1/prompt/{asked.id}/answer", json={"answer": " main "})
    again = client.post(f"/v1/prompt/{asked.id}/answer", json={"answer": "release"})
    not_asking = client.post(f"/v1/prompt/{done.id}/answer", json={"answer": "x"})
    unknown = client.post("/v1/prompt/p_nope/answer", json={"answer": "x"})
    empty = client.post(f"/v1/prompt/{asked.id}/answer", json={"answer": " "})

    # Assert
    assert answered.status_code == HTTPStatus.ACCEPTED
    follow_up = queue.get(answered.json()["prompt_id"])
    assert follow_up is not None and follow_up.prompt == "main" and follow_up.parent_id == asked.id
    assert (again.status_code, again.json()["error"]) == (HTTPStatus.CONFLICT, "already_answered")
    assert (not_asking.status_code, not_asking.json()["error"]) == (
        HTTPStatus.CONFLICT,
        "not_waiting",
    )
    assert unknown.status_code == HTTPStatus.NOT_FOUND
    assert (empty.status_code, empty.json()["error"]) == (HTTPStatus.BAD_REQUEST, "answer_required")


class _UnwritableStore(JsonlPromptJobStore):
    """The record file on a mount that refuses writes while ``writable`` is off."""

    writable = True

    def _append(self, data: bytes) -> None:
        if not self.writable:
            raise PermissionError("read-only mount")
        super()._append(data)


def test_a_prompt_or_answer_the_store_refused_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 202 promises the prompt survives a task replacement; without a record it cannot."""
    # Arrange: a question waiting for an answer, then the mount stops taking writes
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    store = _UnwritableStore(tmp_path / "prompt-jobs.jsonl")
    queue = PromptQueue(store=store)
    webapp.app.state.prompt_queue = queue
    client = TestClient(webapp.app, client=_LOOPBACK)
    asked = queue.submit("fix ci", context={}, actor="u")
    assert asked is not None and queue.take(timeout_seconds=0.01) is asked
    queue.needs_input(asked, "Which branch?")
    store.writable = False

    # Act
    try:
        submitted = client.post("/v1/prompt", json={"prompt": "which tasks run?"})
        answered = client.post(f"/v1/prompt/{asked.id}/answer", json={"answer": "main"})
        store.writable = True
        retried = client.post(f"/v1/prompt/{asked.id}/answer", json={"answer": "main"})
    finally:
        del webapp.app.state.prompt_queue

    # Assert: both refused with a code, nothing left to run, and the question still answerable
    for refused in (submitted, answered):
        assert refused.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert refused.json()["error"] == "prompt_store_unavailable"
    assert retried.status_code == HTTPStatus.ACCEPTED
    assert queue.take(timeout_seconds=0.01) is not None
    assert queue.take(timeout_seconds=0.01) is None
    assert {record["id"] for record in store.load()} == {asked.id, retried.json()["prompt_id"]}
    assert asked.state is PromptState.NEEDS_INPUT
