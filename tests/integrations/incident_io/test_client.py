from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest

from integrations.config_models import IncidentIoIntegrationConfig
from integrations.incident_io.client import (
    IncidentIoClient,
    _get_incident_write_lock,
    make_incident_io_client,
)


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    response.status_code = 200
    return response


@pytest.fixture
def client() -> IncidentIoClient:
    return IncidentIoClient(IncidentIoIntegrationConfig(api_key="test-key"))


def test_config_defaults_to_documented_public_api_url() -> None:
    config = IncidentIoIntegrationConfig(api_key="test-key")

    assert config.base_url == "https://api.incident.io"


def test_config_rejects_non_https_remote_base_url() -> None:
    with pytest.raises(ValueError, match="must use https"):
        IncidentIoIntegrationConfig(
            api_key="test-key",
            base_url="http://169.254.169.254/latest",
        )


def test_make_client_allows_loopback_base_url() -> None:
    created = make_incident_io_client(
        "test-key",
        base_url="http://localhost:8080",
    )
    assert created is not None
    assert created.config.base_url == "http://localhost:8080"


def test_list_incidents_uses_status_category_filter(client: IncidentIoClient, monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        return _response(
            {
                "incidents": [
                    {
                        "id": "inc-123",
                        "reference": "INC-123",
                        "name": "Database outage",
                        "incident_status": {"name": "Mitigating", "category": "live"},
                        "severity": {"name": "SEV1", "rank": 1},
                    }
                ],
                "pagination_meta": {"after": "inc-123", "page_size": 1},
            }
        )

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.list_incidents(status_category="live", page_size=1)

    assert result["success"] is True
    assert result["incidents"][0]["status"] == "Mitigating"
    assert result["incidents"][0]["status_category"] == "live"
    assert calls == [
        (
            "GET",
            "/v2/incidents",
            {"params": {"page_size": 1, "status_category[one_of]": "live"}},
        )
    ]


def test_context_reads_incident_and_updates(client: IncidentIoClient, monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/v2/incidents/inc-123":
            return _response(
                {
                    "incident": {
                        "id": "inc-123",
                        "reference": "INC-123",
                        "name": "Checkout degraded",
                        "summary": "Investigating elevated latency",
                        "incident_status": {"name": "Investigating", "category": "live"},
                    }
                }
            )
        return _response(
            {
                "incident_updates": [
                    {
                        "id": "upd-1",
                        "incident_id": "inc-123",
                        "message": "We are investigating",
                        "new_incident_status": {"name": "Investigating", "category": "live"},
                    }
                ]
            }
        )

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.get_incident_context("inc-123", update_limit=10)

    assert result["success"] is True
    assert result["incident"]["summary"] == "Investigating elevated latency"
    assert result["incident_updates"][0]["message"] == "We are investigating"
    assert calls[0][1] == "/v2/incidents/inc-123"
    assert calls[1] == (
        "GET",
        "/v2/incident_updates",
        {"params": {"incident_id": "inc-123", "page_size": 10}},
    )


def test_context_forwards_pagination_meta_from_updates(
    client: IncidentIoClient, monkeypatch
) -> None:
    """Regression: get_incident_context must forward list_incident_updates's
    pagination_meta so callers can tell total_updates is a page, not a total."""

    def fake_request(method: str, path: str, **kwargs):
        if path == "/v2/incidents/inc-123":
            return _response({"incident": {"id": "inc-123", "name": "Checkout degraded"}})
        return _response(
            {
                "incident_updates": [{"id": "upd-1"}],
                "pagination_meta": {"after": "upd-1", "page_size": 1},
            }
        )

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.get_incident_context("inc-123", update_limit=1)

    assert result["success"] is True
    assert result["pagination_meta"] == {"after": "upd-1", "page_size": 1}


def test_append_summary_update_uses_supported_edit_endpoint(
    client: IncidentIoClient,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, dict]] = []
    summary_store = {"text": "Existing summary"}

    def fake_request(method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return _response({"incident": {"id": "inc-123", "summary": summary_store["text"]}})
        payload = kwargs["json"]
        summary_store["text"] = payload["incident"]["summary"]
        return _response({"incident": {"id": "inc-123"}})

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.append_summary_update(
        "inc-123",
        title="Root cause found",
        body="Bad deploy increased DB latency.",
    )

    assert result["success"] is True
    assert calls[1][0] == "POST"
    assert calls[1][1] == "/v2/incidents/inc-123/actions/edit"
    payload = calls[1][2]["json"]
    assert payload["notify_incident_channel"] is False
    assert "Existing summary" in payload["incident"]["summary"]
    assert "Root cause found" in payload["incident"]["summary"]
    assert calls[2][0] == "GET"
    assert calls[2][1] == "/v2/incidents/inc-123"


def test_append_summary_update_retries_until_verify_sees_finding(
    monkeypatch,
) -> None:
    """Patch class `_request`: instance patches can fail after sleeps in retry loops."""
    client = IncidentIoClient(IncidentIoIntegrationConfig(api_key="test-key"))
    calls: list[tuple[str, str, dict]] = []
    merged_holder = {"text": ""}

    def fake_request(self: IncidentIoClient, method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            get_idx = sum(1 for c in calls if c[0] == "GET")
            if get_idx in {1, 2}:
                summary = "Existing summary"
            else:
                summary = merged_holder["text"]
            return _response({"incident": {"id": "inc-123", "summary": summary}})
        payload = kwargs["json"]
        merged_holder["text"] = payload["incident"]["summary"]
        return _response({"incident": {"id": "inc-123"}})

    sleeps: list[float] = []
    monkeypatch.setattr(IncidentIoClient, "_request", fake_request)
    monkeypatch.setattr("integrations.incident_io.client.time.sleep", sleeps.append)
    monkeypatch.setattr("integrations.incident_io.client.random.random", lambda: 0.0)

    result = client.append_summary_update(
        "inc-123",
        title="Lag spike",
        body="Retry path.",
    )

    assert result["success"] is True
    assert sum(1 for c in calls if c[0] == "POST") == 1
    assert len(sleeps) >= 1


def test_append_summary_update_skips_post_when_finding_already_present(
    client: IncidentIoClient,
    monkeypatch,
) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, _tz=None):
            return datetime(2099, 1, 1, 0, 0, 0, tzinfo=UTC)

    monkeypatch.setattr("integrations.incident_io.client.datetime", FrozenDateTime)

    embedded = "Existing\n\n---\n**OpenSRE finding: Already posted** (2099-01-01 00:00:00 UTC)"
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        return _response({"incident": {"id": "inc-123", "summary": embedded}})

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.append_summary_update(
        "inc-123",
        title="Already posted",
        body="",
    )

    assert result["success"] is True
    assert all(c[0] != "POST" for c in calls)


def test_concurrent_append_summary_uses_shared_module_level_lock() -> None:
    """Two separate client instances targeting the same incident must share one lock."""
    client_a = IncidentIoClient(IncidentIoIntegrationConfig(api_key="key-a"))
    client_b = IncidentIoClient(IncidentIoIntegrationConfig(api_key="key-b"))

    lock_a = _get_incident_write_lock("inc-shared")
    lock_b = _get_incident_write_lock("inc-shared")
    assert lock_a is lock_b, "Different client instances must share the same lock per incident ID"

    lock_other = _get_incident_write_lock("inc-other")
    assert lock_other is not lock_a, "Different incident IDs must use different locks"

    # Verify the clients themselves no longer carry a _write_lock attribute
    assert not hasattr(client_a, "_write_lock")
    assert not hasattr(client_b, "_write_lock")

    # Confirm concurrent writes from two clients are serialised by the shared lock:
    # the first writer holds the lock while the second waits.
    order: list[str] = []
    barrier = threading.Barrier(2)

    lock = _get_incident_write_lock("inc-concurrent")
    results: list[bool] = []

    def holder() -> None:
        with lock:
            order.append("held")
            barrier.wait()

    def waiter() -> None:
        barrier.wait()
        acquired = lock.acquire(blocking=False)
        results.append(acquired)
        if acquired:
            lock.release()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=waiter)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results == [False], "Concurrent writer must be blocked while the lock is held"


def test_request_honors_documented_json_rate_limit_retry_after(
    client: IncidentIoClient,
    monkeypatch,
) -> None:
    retry_at = (datetime.now(UTC) + timedelta(seconds=5)).strftime("%a, %d %b %Y %H:%M:%S UTC")
    rate_limited = _response(
        {
            "type": "too_many_requests",
            "status": 429,
            "rate_limit": {
                "name": "api_key_name",
                "limit": 1200,
                "remaining": 0,
                "retry_after": retry_at,
            },
        }
    )
    rate_limited.status_code = 429
    rate_limited.headers = {}
    rate_limited.raise_for_status.side_effect = httpx.HTTPStatusError(
        "rate limited",
        request=MagicMock(),
        response=rate_limited,
    )
    ok = _response({"incidents": []})

    request = MagicMock(side_effect=[rate_limited, ok])
    fake_http_client = MagicMock()
    fake_http_client.request = request
    monkeypatch.setattr(client, "_get_client", lambda: fake_http_client)

    sleeps: list[float] = []
    monkeypatch.setattr("integrations.incident_io.client.time.sleep", sleeps.append)
    monkeypatch.setattr("integrations.incident_io.client.random.random", lambda: 0.0)

    response = client._request("GET", "/v2/incidents")

    assert response is ok
    assert request.call_count == 2
    assert sleeps
    assert 0 <= sleeps[0] <= 5
