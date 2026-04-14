from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.remote import server as remote_server
from app.remote.server import (
    InvestigateRequest,
    _lifespan,
    investigate,
    investigate_stream,
)
from app.remote.stream import StreamEvent
from app.remote.vercel_poller import VercelResolutionError


@pytest.fixture
def remote_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    monkeypatch.setattr(remote_server, "INVESTIGATIONS_DIR", tmp_path)
    return TestClient(remote_server.app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/investigate", {"json": {"raw_alert": {"alert_name": "High CPU"}}}),
        ("post", "/investigate/stream", {"json": {"raw_alert": {"alert_name": "High CPU"}}}),
        ("get", "/investigations", {}),
        ("get", "/investigations/example", {}),
    ],
)
@pytest.mark.parametrize("configured_key", [None, "", "   "])
def test_protected_remote_endpoints_fail_closed_without_configured_api_key(
    monkeypatch: pytest.MonkeyPatch,
    remote_client: TestClient,
    configured_key: str | None,
    method: str,
    path: str,
    kwargs: dict[str, Any],
) -> None:
    monkeypatch.setattr(remote_server, "_AUTH_KEY", configured_key)

    response = getattr(remote_client, method)(path, **kwargs)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_protected_remote_endpoint_requires_matching_api_key(
    monkeypatch: pytest.MonkeyPatch,
    remote_client: TestClient,
) -> None:
    monkeypatch.setattr(remote_server, "_AUTH_KEY", "secret-key")

    missing_response = remote_client.get("/investigations")
    wrong_response = remote_client.get("/investigations", headers={"x-api-key": "wrong"})
    valid_response = remote_client.get("/investigations", headers={"x-api-key": "secret-key"})

    assert missing_response.status_code == 403
    assert wrong_response.status_code == 403
    assert valid_response.status_code == 200
    assert valid_response.json() == []


@pytest.mark.parametrize("path", ["/ok", "/version", "/health/deep"])
@pytest.mark.parametrize("configured_key", [None, "secret-key"])
def test_health_endpoints_do_not_require_api_key(
    monkeypatch: pytest.MonkeyPatch,
    remote_client: TestClient,
    path: str,
    configured_key: str | None,
) -> None:
    monkeypatch.setattr(remote_server, "_AUTH_KEY", configured_key)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    response = remote_client.get(path)

    assert response.status_code == 200


def test_investigate_enriches_pasted_vercel_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_enrich(raw_alert: dict[str, Any]) -> dict[str, Any]:
        captured["raw_alert"] = raw_alert
        return {
            **raw_alert,
            "alert_name": "Vercel deployment issue: tracer-marketing-website-v3",
            "pipeline_name": "tracer-marketing-website-v3",
            "severity": "critical",
        }

    def fake_execute_investigation(**_kwargs: Any) -> tuple[dict[str, Any], str, str, str]:
        return (
            {"report": "Report body", "root_cause": "Root cause", "problem_md": "Problem"},
            "Vercel deployment issue: tracer-marketing-website-v3",
            "tracer-marketing-website-v3",
            "critical",
        )

    monkeypatch.setattr("app.remote.server.enrich_remote_alert_from_vercel", fake_enrich)
    monkeypatch.setattr(
        "app.remote.server._execute_investigation",
        fake_execute_investigation,
    )
    monkeypatch.setattr("app.remote.server._save_investigation", lambda **_kwargs: None)

    response = investigate(
        InvestigateRequest(
            raw_alert={},
            vercel_url="https://vercel.com/org/tracer-marketing-website-v3/logs?selectedLogId=abc",
        )
    )

    assert captured["raw_alert"]["vercel_url"].startswith("https://vercel.com/")
    assert captured["raw_alert"]["vercel_log_url"].startswith("https://vercel.com/")
    assert response.root_cause == "Root cause"
    assert response.problem_md == "Problem"


def test_investigate_returns_bad_request_for_invalid_vercel_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.remote.server.enrich_remote_alert_from_vercel",
        lambda _raw_alert: (_ for _ in ()).throw(VercelResolutionError("invalid vercel url")),
    )

    with pytest.raises(HTTPException) as exc_info:
        investigate(InvestigateRequest(raw_alert={}, vercel_url="https://vercel.com/example"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid vercel url"


@pytest.mark.asyncio
async def test_investigate_stream_persists_state_on_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: dict[str, Any] = {}

    async def fake_astream_investigation(*args: object, **kwargs: object):
        yield StreamEvent(
            "events",
            data={"data": {"output": {"root_cause": "Schema mismatch", "report": "Fix upstream"}}},
            kind="on_chain_end",
        )
        await asyncio.sleep(0)
        yield StreamEvent("events", data={"data": {}}, kind="on_tool_start")

    def fake_persist_streamed_result(**kwargs: Any) -> None:
        persisted.update(kwargs)

    monkeypatch.setattr("app.config.LLMSettings.from_env", object)
    monkeypatch.setattr(
        "app.cli.investigate.resolve_investigation_context",
        lambda **_kwargs: ("test-alert", "etl_daily_orders", "critical"),
    )
    monkeypatch.setattr(
        "app.pipeline.runners.astream_investigation",
        fake_astream_investigation,
    )
    monkeypatch.setattr(
        "app.remote.server._persist_streamed_result",
        fake_persist_streamed_result,
    )

    response = await investigate_stream(InvestigateRequest(raw_alert={"alert_name": "PayloadAlert"}))
    iterator = response.body_iterator

    first_chunk = await anext(iterator)
    assert first_chunk

    await iterator.aclose()
    await asyncio.sleep(0)

    assert persisted["alert_name"] == "test-alert"
    assert persisted["pipeline_name"] == "etl_daily_orders"
    assert persisted["severity"] == "critical"
    assert persisted["state"]["root_cause"] == "Schema mismatch"
    assert persisted["state"]["report"] == "Fix upstream"


@pytest.mark.asyncio
async def test_lifespan_starts_and_cancels_vercel_poller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _run_forever(self, _handler) -> None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setenv("VERCEL_POLL_ENABLED", "true")
    monkeypatch.setenv("VERCEL_POLL_PROJECT_IDS", "proj_123")
    monkeypatch.setattr("app.remote.server.INVESTIGATIONS_DIR", tmp_path)
    monkeypatch.setattr("app.remote.server.VercelPoller.run_forever", _run_forever)

    async with _lifespan(object()):
        await asyncio.wait_for(started.wait(), timeout=1)

    assert cancelled.is_set()
