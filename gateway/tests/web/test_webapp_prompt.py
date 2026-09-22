"""Tests for ``POST /v1/prompt`` and ``GET /v1/prompt/{id}``: who may call, what is accepted."""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from config.constants.gateway import PROMPT_MAX_CHARS
from gateway.core.prompt_intake import PromptQueue
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
