from __future__ import annotations

from typing import Any

import pytest

from app.integrations.clients.vercel.client import VercelClient, VercelConfig, make_vercel_client


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


def _client(team_id: str = "") -> VercelClient:
    return VercelClient(VercelConfig(api_token="tok_test", team_id=team_id))


def test_is_configured_with_token() -> None:
    assert _client().is_configured is True


def test_is_configured_without_token() -> None:
    c = VercelClient(VercelConfig(api_token=""))
    assert c.is_configured is False


def test_list_projects_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "projects": [
            {"id": "proj_1", "name": "frontend", "framework": "nextjs", "updatedAt": "2024-01-01"},
            {"id": "proj_2", "name": "api", "framework": "", "updatedAt": "2024-01-02"},
        ]
    }
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().list_projects()
    assert result["success"] is True
    assert result["total"] == 2
    assert result["projects"][0]["name"] == "frontend"


def test_list_projects_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "forbidden"}, 403),
    )
    result = _client().list_projects()
    assert result["success"] is False
    assert "403" in result["error"]


def test_list_deployments_filters_state(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"deployments": []})

    monkeypatch.setattr("app.integrations.clients.vercel.client.httpx.Client.get", _fake_get)
    _client().list_deployments(project_id="proj_1", state="error")
    assert captured["params"]["state"] == "ERROR"
    assert captured["params"]["projectId"] == "proj_1"


def test_list_deployments_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "deployments": [
            {
                "uid": "dpl_abc",
                "name": "frontend",
                "url": "frontend-abc.vercel.app",
                "state": "ERROR",
                "createdAt": 1704067200000,
                "ready": None,
                "errorMessage": "Build failed",
                "errorCode": "BUILD_FAILED",
                "meta": {
                    "githubCommitSha": "abc123",
                    "githubCommitMessage": "fix: broken import",
                    "githubCommitRef": "main",
                    "githubRepo": "org/frontend",
                },
            }
        ]
    }
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().list_deployments()
    assert result["success"] is True
    d = result["deployments"][0]
    assert d["id"] == "dpl_abc"
    assert d["state"] == "ERROR"
    assert d["error"] == "Build failed"
    assert d["meta"]["github_commit_sha"] == "abc123"


def test_get_deployment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "id": "dpl_xyz",
        "url": "proj-xyz.vercel.app",
        "name": "proj",
        "readyState": "ERROR",
        "errorMessage": "Function crashed",
        "createdAt": 1704067200000,
        "meta": {},
        "build": {},
    }
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment("dpl_xyz")
    assert result["success"] is True
    assert result["deployment"]["error"] == "Function crashed"


def test_get_deployment_events_list_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"type": "stdout", "created": 1704067200000, "text": "Building..."},
        {"type": "stderr", "created": 1704067201000, "text": "Error: module not found"},
    ]
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment_events("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 2
    assert result["events"][1]["text"] == "Error: module not found"


def test_get_runtime_logs_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"id": "log_1", "createdAt": 1704067200000, "payload": {"text": "invoked"}, "type": "request", "source": "lambda"},
    ]
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_runtime_logs("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["logs"][0]["id"] == "log_1"


def test_team_params_included_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"projects": []})

    monkeypatch.setattr("app.integrations.clients.vercel.client.httpx.Client.get", _fake_get)
    _client(team_id="team_123").list_projects()
    assert captured["params"]["teamId"] == "team_123"


def test_team_params_absent_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"projects": []})

    monkeypatch.setattr("app.integrations.clients.vercel.client.httpx.Client.get", _fake_get)
    _client(team_id="").list_projects()
    assert "teamId" not in captured["params"]


def test_get_deployment_events_dict_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "events": [
            {"type": "stdout", "created": 1, "text": "start"},
            {"type": "stderr", "created": 2, "payload": {"text": "from payload field"}},
        ]
    }
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment_events("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 2
    assert result["events"][0]["text"] == "start"
    assert result["events"][1]["text"] == "from payload field"


def test_get_runtime_logs_dict_wrapped_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"logs": [{"id": "l1", "createdAt": 1, "payload": {}, "type": "stdout", "source": "lambda"}]}
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_runtime_logs("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 1


def test_list_projects_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_self: Any, _path: str, **_kw: Any) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.integrations.clients.vercel.client.httpx.Client.get", _raise)
    result = _client().list_projects()
    assert result["success"] is False
    assert "connection refused" in result["error"]


def test_get_deployment_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_self: Any, _path: str, **_kw: Any) -> _FakeResponse:
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("app.integrations.clients.vercel.client.httpx.Client.get", _raise)
    result = _client().get_deployment("dpl_xyz")
    assert result["success"] is False
    assert "timed out" in result["error"]


def test_list_deployments_no_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"deployments": []})

    monkeypatch.setattr("app.integrations.clients.vercel.client.httpx.Client.get", _fake_get)
    _client().list_deployments()
    assert "state" not in captured["params"]
    assert "projectId" not in captured["params"]


def test_get_deployment_events_null_payload_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"type": "stdout", "created": 1, "text": "normal text", "payload": None},
        {"type": "stderr", "created": 2, "text": None, "payload": None},
        {"type": "stdout", "created": 3, "text": None, "payload": {"text": "from payload"}},
    ]
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment_events("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 3
    assert result["events"][0]["text"] == "normal text"
    assert result["events"][1]["text"] == ""
    assert result["events"][2]["text"] == "from payload"


def test_get_deployment_events_text_is_always_string(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"type": "stdout", "created": 1, "text": 42, "payload": None}]
    monkeypatch.setattr(
        "app.integrations.clients.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment_events("dpl_xyz")
    assert isinstance(result["events"][0]["text"], str)
    assert result["events"][0]["text"] == "42"


def test_close_releases_http_client() -> None:
    c = _client()
    # Force-initialize the internal client
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


def test_make_vercel_client_returns_client_with_valid_token() -> None:
    client = make_vercel_client("tok_test")
    assert client is not None
    assert client.is_configured is True


def test_make_vercel_client_returns_none_for_empty_token() -> None:
    assert make_vercel_client("") is None
    assert make_vercel_client(None) is None


def test_make_vercel_client_returns_none_for_whitespace_token() -> None:
    assert make_vercel_client("   ") is None


def test_make_vercel_client_forwards_team_id() -> None:
    client = make_vercel_client("tok_test", "team_xyz")
    assert client is not None
    assert client.config.team_id == "team_xyz"


def test_context_manager_closes_on_exception() -> None:
    c = _client()
    with pytest.raises(ValueError), c:
        _ = c._get_client()
        raise ValueError("test error")
    assert c._client is None
