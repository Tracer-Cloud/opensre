from __future__ import annotations

from typing import Any

import pytest

from app.services.opsgenie.client import (
    OpsGenieClient,
    OpsGenieConfig,
    make_opsgenie_client,
)


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)[:200]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._payload


def _client(region: str = "us") -> OpsGenieClient:
    return OpsGenieClient(OpsGenieConfig(api_key="test-genie-key", region=region))


# --- Config ---


def test_is_configured_with_key() -> None:
    assert _client().is_configured is True


def test_is_configured_without_key() -> None:
    c = OpsGenieClient(OpsGenieConfig(api_key=""))
    assert c.is_configured is False


def test_region_defaults_to_us() -> None:
    c = _client()
    assert c.config.region == "us"
    assert c.config.base_url == "https://api.opsgenie.com"


def test_eu_region_base_url() -> None:
    c = _client(region="eu")
    assert c.config.base_url == "https://api.eu.opsgenie.com"


def test_invalid_region_falls_back_to_us() -> None:
    c = OpsGenieClient(OpsGenieConfig(api_key="k", region="invalid"))
    assert c.config.region == "us"
    assert c.config.base_url == "https://api.opsgenie.com"


def test_headers_include_genie_key() -> None:
    c = _client()
    assert c.config.headers["Authorization"] == "GenieKey test-genie-key"


# --- list_alerts ---


def test_list_alerts_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": [
            {
                "id": "a1",
                "tinyId": "1",
                "message": "CPU high",
                "status": "open",
                "acknowledged": False,
                "isSeen": True,
                "priority": "P1",
                "source": "Datadog",
                "tags": ["env:prod"],
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-01T01:00:00Z",
                "owner": "oncall@team.com",
                "integration": {"type": "Datadog"},
            },
        ]
    }
    monkeypatch.setattr(
        "app.services.opsgenie.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().list_alerts()
    assert result["success"] is True
    assert result["total"] == 1
    assert result["alerts"][0]["message"] == "CPU high"
    assert result["alerts"][0]["priority"] == "P1"


def test_list_alerts_with_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"data": []})

    monkeypatch.setattr("app.services.opsgenie.client.httpx.Client.get", _fake_get)
    _client().list_alerts(query="status=open", limit=5)
    assert captured["params"]["query"] == "status=open"
    assert captured["params"]["limit"] == 5


def test_list_alerts_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.opsgenie.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"message": "forbidden"}, 403),
    )
    result = _client().list_alerts()
    assert result["success"] is False
    assert "403" in result["error"]


def test_list_alerts_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_self: Any, _path: str, **_kw: Any) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.opsgenie.client.httpx.Client.get", _raise)
    result = _client().list_alerts()
    assert result["success"] is False
    assert "connection refused" in result["error"]


# --- get_alert ---


def test_get_alert_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": {
            "id": "a1",
            "tinyId": "1",
            "message": "CPU high",
            "description": "CPU usage exceeded 90%",
            "status": "open",
            "acknowledged": False,
            "isSeen": True,
            "priority": "P1",
            "source": "Datadog",
            "tags": ["env:prod"],
            "teams": [{"id": "team1"}],
            "responders": [{"type": "user", "id": "u1"}],
            "actions": ["Acknowledge"],
            "details": {"region": "us-east-1"},
            "alias": "cpu-high-prod",
            "entity": "prod-api",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T01:00:00Z",
            "count": 3,
            "owner": "oncall@team.com",
            "integration": {"type": "Datadog"},
        }
    }
    monkeypatch.setattr(
        "app.services.opsgenie.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_alert("a1")
    assert result["success"] is True
    assert result["alert"]["description"] == "CPU usage exceeded 90%"
    assert result["alert"]["details"] == {"region": "us-east-1"}
    assert result["alert"]["teams"] == ["team1"]


def test_get_alert_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.opsgenie.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"message": "not found"}, 404),
    )
    result = _client().get_alert("bad-id")
    assert result["success"] is False
    assert "404" in result["error"]


def test_get_alert_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_self: Any, _path: str, **_kw: Any) -> _FakeResponse:
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("app.services.opsgenie.client.httpx.Client.get", _raise)
    result = _client().get_alert("a1")
    assert result["success"] is False
    assert "timed out" in result["error"]


# --- get_alert_logs ---


def test_get_alert_logs_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": [
            {
                "log": "Alert created",
                "type": "system",
                "owner": "system",
                "createdAt": "2024-01-01T00:00:00Z",
                "offset": "0",
            },
            {
                "log": "Acknowledged by oncall",
                "type": "alertRecipient",
                "owner": "user@team.com",
                "createdAt": "2024-01-01T00:05:00Z",
                "offset": "1",
            },
        ]
    }
    monkeypatch.setattr(
        "app.services.opsgenie.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_alert_logs("a1")
    assert result["success"] is True
    assert result["total"] == 2
    assert result["logs"][0]["log"] == "Alert created"


# --- add_note ---


def test_add_note_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.opsgenie.client.httpx.Client.post",
        lambda _self, _path, **_kw: _FakeResponse(
            {"result": "Request will be processed", "requestId": "r1"}
        ),
    )
    result = _client().add_note("a1", "Investigation complete")
    assert result["success"] is True


def test_add_note_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.opsgenie.client.httpx.Client.post",
        lambda _self, _path, **_kw: _FakeResponse({"message": "forbidden"}, 403),
    )
    result = _client().add_note("a1", "note")
    assert result["success"] is False
    assert "403" in result["error"]


# --- close / context manager ---


def test_close_releases_http_client() -> None:
    c = _client()
    _ = c._get_client()
    assert c._client is not None
    c.close()
    assert c._client is None


def test_close_is_idempotent() -> None:
    c = _client()
    c.close()
    c.close()  # should not raise


def test_context_manager_closes_on_exit() -> None:
    with _client() as c:
        _ = c._get_client()
        assert c._client is not None
    assert c._client is None


def _raise_value_error() -> None:
    raise ValueError("test error")


def test_context_manager_closes_on_exception() -> None:
    c = _client()
    _ = c._get_client()
    with pytest.raises(ValueError), c:
        _raise_value_error()
    assert c._client is None


# --- make_opsgenie_client ---


def test_make_client_returns_client_with_valid_key() -> None:
    client = make_opsgenie_client("test-key")
    assert client is not None
    assert client.is_configured is True


def test_make_client_returns_none_for_empty_key() -> None:
    assert make_opsgenie_client("") is None
    assert make_opsgenie_client(None) is None


def test_make_client_returns_none_for_whitespace_key() -> None:
    assert make_opsgenie_client("   ") is None


def test_make_client_forwards_region() -> None:
    client = make_opsgenie_client("test-key", "eu")
    assert client is not None
    assert client.config.region == "eu"
