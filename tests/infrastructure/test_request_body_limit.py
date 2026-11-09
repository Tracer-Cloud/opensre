"""The request-body cap must hold even when Content-Length does not declare it."""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from config.constants.http import MAX_REQUEST_BODY_BYTES
from infrastructure.request_body_limit import RequestBodyLimitMiddleware


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"read": len(await request.body())}

    return TestClient(app)


def _chunks(total: int) -> Iterator[bytes]:
    """Stream ``total`` bytes, which makes httpx send no Content-Length."""
    sent = 0
    while sent < total:
        chunk = b"x" * min(64 * 1024, total - sent)
        sent += len(chunk)
        yield chunk


def test_streamed_body_without_content_length_is_capped(client: TestClient) -> None:
    """The gap the route-level check missed: no Content-Length, so nothing rejected early.

    The handler used to buffer the whole payload before any size check ran, so
    peak memory was unbounded no matter what the post-read check then said.
    """
    resp = client.post("/echo", content=_chunks(MAX_REQUEST_BODY_BYTES + 1))

    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert resp.json() == {"error": "payload too large"}


def test_body_at_the_limit_still_passes(client: TestClient) -> None:
    """The cap is inclusive; a payload exactly at the limit is a legitimate request."""
    resp = client.post("/echo", content=b"x" * MAX_REQUEST_BODY_BYTES)

    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"read": MAX_REQUEST_BODY_BYTES}
