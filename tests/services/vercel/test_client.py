from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.vercel.client import (
    _MAX_VERCEL_PATH_SEGMENT_LEN,
    VercelClient,
    VercelConfig,
    _append_parsed_runtime_stream_value,
    _ingest_runtime_log_stream_line,
    _safe_vercel_path_segment,
    make_vercel_client,
)


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200, *, text: str | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else str(payload)[:200]

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


class _FakeStreamResponse:
    """Minimal streaming response for ``Client.stream`` (runtime logs)."""

    def __init__(
        self,
        *,
        lines: list[str],
        status_code: int = 200,
        iter_exc: type[BaseException] | None = None,
        iter_msg: str = "",
    ) -> None:
        self._lines = lines
        self.status_code = status_code
        self._iter_exc = iter_exc
        self._iter_msg = iter_msg

    def __enter__(self) -> _FakeStreamResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=self,  # type: ignore[arg-type]
            )

    def iter_lines(self) -> Any:
        if self._iter_exc is not None:
            raise self._iter_exc(self._iter_msg)
        yield from self._lines


def _runtime_stream_lines_from_payload(payload: Any) -> list[str]:
    return [json.dumps(payload)]


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
        "app.services.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().list_projects()
    assert result["success"] is True
    assert result["total"] == 2
    assert result["projects"][0]["name"] == "frontend"


def test_list_projects_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.vercel.client.httpx.Client.get",
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

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.get", _fake_get)
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
        "app.services.vercel.client.httpx.Client.get",
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
        "app.services.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment("dpl_xyz")
    assert result["success"] is True
    assert result["deployment"]["error"] == "Function crashed"


def test_get_deployment_normalizes_git_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "id": "dpl_xyz",
        "url": "proj-xyz.vercel.app",
        "name": "proj",
        "readyState": "ERROR",
        "errorMessage": "Function crashed",
        "createdAt": 1704067200000,
        "meta": {
            "githubCommitSha": "abc123",
            "githubCommitRef": "main",
            "githubRepo": "org/proj",
        },
        "build": {},
    }
    monkeypatch.setattr(
        "app.services.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment("dpl_xyz")
    assert result["success"] is True
    assert result["deployment"]["meta"]["github_commit_sha"] == "abc123"
    assert result["deployment"]["meta"]["github_commit_ref"] == "main"
    assert result["deployment"]["meta"]["github_repo"] == "org/proj"
    assert result["deployment"]["raw_meta"]["githubCommitSha"] == "abc123"


def test_get_deployment_events_list_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"id": "evt_1", "type": "stdout", "created": 1704067200000, "text": "Building..."},
        {
            "id": "evt_2",
            "type": "stderr",
            "created": 1704067201000,
            "text": "Error: module not found",
        },
    ]
    monkeypatch.setattr(
        "app.services.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment_events("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 2
    assert result["events"][0]["id"] == "evt_1"
    assert result["events"][1]["text"] == "Error: module not found"


def test_get_runtime_logs_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    payload = [
        {
            "rowId": "log_1",
            "timestampInMs": 1704067200000,
            "message": "invoked",
            "level": "error",
            "source": "request",
            "requestPath": "/app-includes/css/buttons.css",
            "responseStatusCode": 404,
        },
    ]

    def _fake_stream(_self: Any, _method: str, path: str, **_kw: Any) -> _FakeStreamResponse:
        captured["path"] = path
        return _FakeStreamResponse(lines=_runtime_stream_lines_from_payload(payload))

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.stream", _fake_stream)
    result = _client().get_runtime_logs("dpl_xyz", project_id="proj_123")
    assert result["success"] is True
    assert result["total"] == 1
    assert result["logs"][0]["id"] == "log_1"
    assert result["logs"][0]["message"] == "invoked"
    assert result["logs"][0]["level"] == "error"
    assert result["logs"][0]["status_code"] == 404
    assert result["logs"][0]["request_path"] == "/app-includes/css/buttons.css"
    assert captured["path"] == "/v1/projects/proj_123/deployments/dpl_xyz/runtime-logs"


def test_get_runtime_logs_stream_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        (
            '{"rowId":"log_1","timestampInMs":1704067200000,"message":"Error loading resource",'
            '"level":"error","source":"request","requestPath":"/foo","responseStatusCode":404}'
        ),
        (
            '{"rowId":"log_2","timestampInMs":1704067201000,"message":"ok","level":"info",'
            '"source":"request","requestPath":"/bar","responseStatusCode":200}'
        ),
    ]
    monkeypatch.setattr(
        "app.services.vercel.client.httpx.Client.stream",
        lambda _self, *_a, **_kw: _FakeStreamResponse(lines=lines),
    )
    result = _client().get_runtime_logs("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 2
    assert result["logs"][0]["message"] == "Error loading resource"
    assert result["logs"][0]["level"] == "error"
    assert result["logs"][1]["id"] == "log_2"


def test_get_runtime_logs_404_returns_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.vercel.client.httpx.Client.stream",
        lambda _self, *_a, **_kw: _FakeStreamResponse(lines=[], status_code=404),
    )
    result = _client().get_runtime_logs("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 0
    assert result["logs"] == []


def test_team_params_included_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"projects": []})

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.get", _fake_get)
    _client(team_id="team_123").list_projects()
    assert captured["params"]["teamId"] == "team_123"


def test_team_params_absent_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"projects": []})

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.get", _fake_get)
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
        "app.services.vercel.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_deployment_events("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 2
    assert result["events"][0]["text"] == "start"
    assert result["events"][1]["text"] == "from payload field"


def test_get_runtime_logs_dict_wrapped_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "logs": [{"id": "l1", "createdAt": 1, "payload": {}, "type": "stdout", "source": "lambda"}]
    }
    monkeypatch.setattr(
        "app.services.vercel.client.httpx.Client.stream",
        lambda _self, *_a, **_kw: _FakeStreamResponse(
            lines=_runtime_stream_lines_from_payload(payload)
        ),
    )
    result = _client().get_runtime_logs("dpl_xyz")
    assert result["success"] is True
    assert result["total"] == 1


def test_get_runtime_logs_retries_on_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    payload = [
        {
            "rowId": "log_1",
            "timestampInMs": 1,
            "message": "ok",
            "level": "error",
            "source": "request",
            "requestPath": "/x",
            "responseStatusCode": 404,
        },
    ]

    def _fake_stream(_self: Any, *_a: Any, **_kw: Any) -> _FakeStreamResponse:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow")
        return _FakeStreamResponse(lines=_runtime_stream_lines_from_payload(payload))

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.stream", _fake_stream)
    monkeypatch.setattr("app.services.vercel.client.time.sleep", lambda _s: None)
    result = _client().get_runtime_logs("dpl_xyz", project_id="proj_123")
    assert result["success"] is True
    assert result["total"] == 1
    assert calls["n"] == 3


def test_get_runtime_logs_retries_on_remote_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    payload = [
        {
            "rowId": "log_1",
            "timestampInMs": 1,
            "message": "ok",
            "level": "error",
            "source": "request",
            "requestPath": "/x",
            "responseStatusCode": 404,
        },
    ]

    def _fake_stream(_self: Any, *_a: Any, **_kw: Any) -> _FakeStreamResponse:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return _FakeStreamResponse(lines=_runtime_stream_lines_from_payload(payload))

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.stream", _fake_stream)
    monkeypatch.setattr("app.services.vercel.client.time.sleep", lambda _s: None)
    result = _client().get_runtime_logs("dpl_xyz", project_id="proj_123")
    assert result["success"] is True
    assert result["total"] == 1
    assert calls["n"] == 3


def test_get_runtime_logs_remote_protocol_error_after_retries_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_stream(_self: Any, *_a: Any, **_kw: Any) -> _FakeStreamResponse:
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.stream", _fake_stream)
    monkeypatch.setattr("app.services.vercel.client.time.sleep", lambda _s: None)

    result = _client().get_runtime_logs("dpl_xyz", project_id="proj_123")

    assert result["success"] is False
    assert "Server disconnected without sending a response." in result["error"]
    assert "after 3 attempts while reading runtime logs" in result["error"]


def test_list_projects_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_self: Any, _path: str, **_kw: Any) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.get", _raise)
    result = _client().list_projects()
    assert result["success"] is False
    assert "connection refused" in result["error"]


def test_get_deployment_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_self: Any, _path: str, **_kw: Any) -> _FakeResponse:
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.get", _raise)
    result = _client().get_deployment("dpl_xyz")
    assert result["success"] is False
    assert "timed out" in result["error"]


def test_get_deployment_rejects_unsafe_deployment_id() -> None:
    result = _client().get_deployment("dpl_../other")
    assert result["success"] is False
    assert "invalid" in str(result.get("error", "")).lower()


def test_get_deployment_events_rejects_unsafe_deployment_id() -> None:
    result = _client().get_deployment_events("x/y")
    assert result["success"] is False
    assert "invalid" in str(result.get("error", "")).lower()


def test_get_runtime_logs_rejects_unsafe_project_id() -> None:
    result = _client().get_runtime_logs("dpl_xyz", project_id="prj/../x")
    assert result["success"] is False
    assert "invalid" in str(result.get("error", "")).lower()


def test_list_deployments_no_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"deployments": []})

    monkeypatch.setattr("app.services.vercel.client.httpx.Client.get", _fake_get)
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
        "app.services.vercel.client.httpx.Client.get",
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
        "app.services.vercel.client.httpx.Client.get",
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


def _raise_value_error() -> None:
    raise ValueError("test error")


def test_context_manager_closes_on_exception() -> None:
    c = _client()
    _ = c._get_client()
    with pytest.raises(ValueError), c:
        _raise_value_error()
    assert c._client is None


def test_ingest_runtime_log_stream_line_parses_data_prefixed_json() -> None:
    """Test that lines prefixed with 'data:' are correctly parsed and added to the bucket."""
    bucket: list[dict[str, Any]] = []
    line = 'data:{"id":"log_1","message":"test log","level":"info"}'
    result = _ingest_runtime_log_stream_line(line, bucket, limit=10)
    assert result is False  # bucket not full yet
    assert len(bucket) == 1
    assert bucket[0]["id"] == "log_1"
    assert bucket[0]["message"] == "test log"
    assert bucket[0]["level"] == "info"


def test_ingest_runtime_log_stream_line_parses_json_without_data_prefix() -> None:
    """Test that lines without 'data:' prefix are also correctly parsed."""
    bucket: list[dict[str, Any]] = []
    line = '{"id":"log_2","message":"another log","level":"error"}'
    result = _ingest_runtime_log_stream_line(line, bucket, limit=10)
    assert result is False
    assert len(bucket) == 1
    assert bucket[0]["id"] == "log_2"
    assert bucket[0]["message"] == "another log"
    assert bucket[0]["level"] == "error"


def test_ingest_runtime_log_stream_line_handles_data_prefix_with_whitespace() -> None:
    """Test that 'data:' prefix followed by whitespace is correctly handled."""
    bucket: list[dict[str, Any]] = []
    line = 'data:  {"id":"log_3","message":"spaced log","level":"warn"}'
    result = _ingest_runtime_log_stream_line(line, bucket, limit=10)
    assert result is False
    assert len(bucket) == 1
    assert bucket[0]["id"] == "log_3"
    assert bucket[0]["message"] == "spaced log"
    assert bucket[0]["level"] == "warn"


def test_ingest_runtime_log_stream_line_respects_limit() -> None:
    """Test that the limit is respected and parsing stops when bucket is full."""
    bucket: list[dict[str, Any]] = []
    limit = 2

    # First line should be added
    line1 = 'data:{"id":"log_1","message":"first"}'
    result1 = _ingest_runtime_log_stream_line(line1, bucket, limit=limit)
    assert result1 is False
    assert len(bucket) == 1

    # Second line should be added, reaching limit
    line2 = 'data:{"id":"log_2","message":"second"}'
    result2 = _ingest_runtime_log_stream_line(line2, bucket, limit=limit)
    assert result2 is True  # bucket is now full
    assert len(bucket) == 2

    # Third line: the helper still appends for a single dict (no pre-check),
    # but the stream-collection layer's bucket[:limit] slice caps the final result.
    line3 = 'data:{"id":"log_3","message":"third"}'
    result3 = _ingest_runtime_log_stream_line(line3, bucket, limit=limit)
    assert result3 is True  # bucket is over limit, indicating stop
    assert len(bucket) == 3  # note: helper over-appends, collector slices to limit


def test_ingest_runtime_log_stream_line_handles_empty_lines() -> None:
    """Test that empty lines are safely ignored without affecting the bucket."""
    bucket: list[dict[str, Any]] = [{"id": "existing"}]
    line = ""
    result = _ingest_runtime_log_stream_line(line, bucket, limit=10)
    assert result is False  # bucket not full
    assert len(bucket) == 1
    assert bucket[0]["id"] == "existing"


def test_ingest_runtime_log_stream_line_handles_invalid_json_gracefully() -> None:
    """Test that invalid JSON is safely ignored without crashing."""
    bucket: list[dict[str, Any]] = []
    line = "data:not valid json"
    result = _ingest_runtime_log_stream_line(line, bucket, limit=10)
    assert result is False
    assert len(bucket) == 0


def test_ingest_runtime_log_stream_line_handles_whitespace_only_lines() -> None:
    """Test that whitespace-only lines are safely ignored."""
    bucket: list[dict[str, Any]] = [{"id": "existing"}]
    line = "   \n\t  "
    result = _ingest_runtime_log_stream_line(line, bucket, limit=10)
    assert result is False
    assert len(bucket) == 1
    assert bucket[0]["id"] == "existing"


def test_append_parsed_runtime_stream_value_expands_nested_logs_list() -> None:
    """Test that a dict with a 'logs' key is expanded into individual items."""
    bucket: list[dict[str, Any]] = []
    parsed = {"logs": [{"id": "log_1"}, {"id": "log_2"}, {"id": "log_3"}]}
    _append_parsed_runtime_stream_value(parsed, bucket, limit=10)
    assert len(bucket) == 3
    assert bucket[0]["id"] == "log_1"
    assert bucket[1]["id"] == "log_2"
    assert bucket[2]["id"] == "log_3"


def test_append_parsed_runtime_stream_value_respects_limit_with_nested_logs() -> None:
    """Test that limit is respected when expanding nested logs list."""
    bucket: list[dict[str, Any]] = []
    parsed = {"logs": [{"id": f"log_{i}"} for i in range(10)]}
    _append_parsed_runtime_stream_value(parsed, bucket, limit=3)
    assert len(bucket) == 3
    assert bucket[0]["id"] == "log_0"
    assert bucket[2]["id"] == "log_2"


def test_append_parsed_runtime_stream_value_appends_single_dict() -> None:
    """Test that a single dict without 'logs' key is appended directly."""
    bucket: list[dict[str, Any]] = []
    parsed = {"id": "single_log", "message": "test"}
    _append_parsed_runtime_stream_value(parsed, bucket, limit=10)
    assert len(bucket) == 1
    assert bucket[0]["id"] == "single_log"
    assert bucket[0]["message"] == "test"


def test_append_parsed_runtime_stream_value_expands_list_of_dicts() -> None:
    """Test that a list of dicts is expanded into individual items."""
    bucket: list[dict[str, Any]] = []
    parsed = [{"id": "log_1"}, {"id": "log_2"}, {"id": "log_3"}]
    _append_parsed_runtime_stream_value(parsed, bucket, limit=10)
    assert len(bucket) == 3
    assert bucket[0]["id"] == "log_1"
    assert bucket[1]["id"] == "log_2"
    assert bucket[2]["id"] == "log_3"


def test_append_parsed_runtime_stream_value_respects_limit_with_list() -> None:
    """Test that limit is respected when expanding a list of dicts."""
    bucket: list[dict[str, Any]] = []
    parsed = [{"id": f"log_{i}"} for i in range(10)]
    _append_parsed_runtime_stream_value(parsed, bucket, limit=2)
    assert len(bucket) == 2
    assert bucket[0]["id"] == "log_0"
    assert bucket[1]["id"] == "log_1"


# ---------------------------------------------------------------------------
# _safe_vercel_path_segment
# ---------------------------------------------------------------------------


class TestSafeVercelPathSegment:
    """Unit tests for _safe_vercel_path_segment()."""

    # -- invalid inputs that must return None --

    def test_empty_string_returns_none(self) -> None:
        assert _safe_vercel_path_segment("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _safe_vercel_path_segment("   ") is None

    def test_tab_only_returns_none(self) -> None:
        assert _safe_vercel_path_segment("\t") is None

    def test_too_long_string_returns_none(self) -> None:
        assert _safe_vercel_path_segment("a" * (_MAX_VERCEL_PATH_SEGMENT_LEN + 1)) is None

    def test_exactly_max_length_is_valid(self) -> None:
        segment = "a" * _MAX_VERCEL_PATH_SEGMENT_LEN
        assert _safe_vercel_path_segment(segment) == segment

    def test_contains_double_dot_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl_abc..def") is None

    def test_path_traversal_returns_none(self) -> None:
        assert _safe_vercel_path_segment("../secret") is None

    def test_double_dot_at_end_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl_abc..") is None

    def test_double_slash_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl_abc//def") is None

    def test_single_slash_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl/abc") is None

    def test_starts_with_dot_returns_none(self) -> None:
        assert _safe_vercel_path_segment(".hidden") is None

    def test_starts_with_hyphen_returns_none(self) -> None:
        assert _safe_vercel_path_segment("-abc") is None

    def test_starts_with_underscore_returns_none(self) -> None:
        assert _safe_vercel_path_segment("_abc") is None

    def test_space_in_middle_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl abc") is None

    def test_null_byte_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl\x00abc") is None

    def test_newline_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl\nabc") is None

    def test_special_chars_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl@abc!") is None

    def test_colon_returns_none(self) -> None:
        assert _safe_vercel_path_segment("dpl:abc") is None

    # -- valid inputs that must return the cleaned string --

    def test_simple_alphanumeric_id(self) -> None:
        assert _safe_vercel_path_segment("dpl123") == "dpl123"

    def test_id_with_underscores_and_hyphens(self) -> None:
        assert _safe_vercel_path_segment("dpl_abc-123") == "dpl_abc-123"

    def test_id_with_dots(self) -> None:
        assert _safe_vercel_path_segment("dpl.abc.1") == "dpl.abc.1"

    def test_single_character_id(self) -> None:
        assert _safe_vercel_path_segment("a") == "a"

    def test_mixed_case_id(self) -> None:
        assert _safe_vercel_path_segment("DplAbc123") == "DplAbc123"

    def test_strips_surrounding_whitespace(self) -> None:
        assert _safe_vercel_path_segment("  dpl_abc  ") == "dpl_abc"

    def test_strips_leading_whitespace(self) -> None:
        assert _safe_vercel_path_segment("   dpl123") == "dpl123"

    def test_strips_trailing_whitespace(self) -> None:
        assert _safe_vercel_path_segment("dpl123   ") == "dpl123"

    def test_realistic_vercel_deployment_id(self) -> None:
        assert (
            _safe_vercel_path_segment("dpl_7JtoAHRqD4xSGBDT6MrFxXZH")
            == "dpl_7JtoAHRqD4xSGBDT6MrFxXZH"
        )

    def test_realistic_vercel_project_id(self) -> None:
        assert _safe_vercel_path_segment("prj_AbCdEfGh12345678") == "prj_AbCdEfGh12345678"

    def test_single_dot_is_valid(self) -> None:
        """A single dot between characters is allowed; only '..' triggers the guard."""
        assert _safe_vercel_path_segment("a.b") == "a.b"
