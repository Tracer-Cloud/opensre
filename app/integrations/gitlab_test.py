"""Tests for shared gitlab integration helpers."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.integrations.gitlab import (
    DEFAULT_GITLAB_BASE_URL,
    GitlabConfig,
    GitlabValidationResult,
    build_gitlab_config,
    get_gitlab_commits,
    get_gitlab_file,
    get_gitlab_mrs,
    get_gitlab_pipelines,
    gitlab_config_from_env,
    validate_gitlab_config,
    validate_gitlab_connection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "https://gitlab.com"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._payload


def _make_config(**kwargs: Any) -> GitlabConfig:
    return GitlabConfig.model_validate({"auth_token": "test-token", **kwargs})


# ---------------------------------------------------------------------------
# GitlabConfig
# ---------------------------------------------------------------------------


def test_gitlab_config_defaults() -> None:
    config = _make_config()

    assert config.base_url == DEFAULT_GITLAB_BASE_URL
    assert config.timeout_seconds == 15.0


def test_gitlab_config_api_base_url_strips_trailing_slash() -> None:
    config = _make_config(base_url="https://gitlab.example.com/api/v4/")

    assert config.api_base_url == "https://gitlab.example.com/api/v4"


def test_gitlab_config_auth_headers_include_bearer_token() -> None:
    config = _make_config(auth_token="my-secret-token")

    assert config.auth_headers == {
        "Authorization": "Bearer my-secret-token",
        "Accept": "application/json",
    }


def test_gitlab_config_normalizes_empty_base_url_to_default() -> None:
    config = _make_config(base_url="")

    assert config.base_url == DEFAULT_GITLAB_BASE_URL


def test_gitlab_config_normalizes_none_base_url_to_default() -> None:
    config = GitlabConfig.model_validate({"auth_token": "tok", "base_url": None})

    assert config.base_url == DEFAULT_GITLAB_BASE_URL


def test_gitlab_config_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        _make_config(timeout_seconds=0)


def test_gitlab_config_rejects_unknown_fields_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="auth_toke.*auth_token"):
        GitlabConfig.model_validate({"auth_toke": "tok"})


# ---------------------------------------------------------------------------
# build_gitlab_config / gitlab_config_from_env
# ---------------------------------------------------------------------------


def test_build_gitlab_config_accepts_empty_dict() -> None:
    config = build_gitlab_config({})

    assert config.base_url == DEFAULT_GITLAB_BASE_URL
    assert config.auth_token == ""


def test_build_gitlab_config_accepts_none() -> None:
    config = build_gitlab_config(None)

    assert config.base_url == DEFAULT_GITLAB_BASE_URL


def test_gitlab_config_from_env_returns_none_when_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)

    assert gitlab_config_from_env() is None


def test_gitlab_config_from_env_builds_config_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "gl-token")
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com/api/v4")

    config = gitlab_config_from_env()

    assert config is not None
    assert config.auth_token == "gl-token"
    assert config.base_url == "https://gitlab.example.com/api/v4"


def test_gitlab_config_from_env_uses_default_url_when_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "gl-token")
    monkeypatch.delenv("GITLAB_URL", raising=False)

    config = gitlab_config_from_env()

    assert config is not None
    assert config.base_url == DEFAULT_GITLAB_BASE_URL


# ---------------------------------------------------------------------------
# validate_gitlab_config
# ---------------------------------------------------------------------------


def test_validate_gitlab_config_fails_when_token_missing() -> None:
    config = build_gitlab_config({})

    result = validate_gitlab_config(config)

    assert result.ok is False
    assert "auth token is required" in result.detail


def test_validate_gitlab_config_returns_ok_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.gitlab.validate_gitlab_connection",
        lambda *, config: {"username": "gl-user"},
    )

    result = validate_gitlab_config(_make_config())

    assert result.ok is True
    assert "@gl-user" in result.detail


def test_validate_gitlab_config_handles_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*, config: GitlabConfig) -> None:
        response = httpx.Response(401)
        response._content = b"Unauthorized"
        raise httpx.HTTPStatusError(
            "401",
            request=httpx.Request("GET", "https://gitlab.com/api/v4/user"),
            response=response,
        )

    monkeypatch.setattr("app.integrations.gitlab.validate_gitlab_connection", _raise)

    result = validate_gitlab_config(_make_config())

    assert result.ok is False
    assert "validation failed" in result.detail


def test_validate_gitlab_config_handles_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.gitlab.validate_gitlab_connection",
        lambda *, config: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    result = validate_gitlab_config(_make_config())

    assert result.ok is False
    assert "connection refused" in result.detail


# ---------------------------------------------------------------------------
# validate_gitlab_connection
# ---------------------------------------------------------------------------


def test_validate_gitlab_connection_returns_user_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse({"id": 1, "username": "gl-user"}),
    )

    result = validate_gitlab_connection(config=_make_config())

    assert result == {"id": 1, "username": "gl-user"}


def test_validate_gitlab_connection_returns_empty_dict_for_non_dict_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse(["unexpected", "list"]),
    )

    result = validate_gitlab_connection(config=_make_config())

    assert result == {}


# ---------------------------------------------------------------------------
# get_gitlab_commits
# ---------------------------------------------------------------------------


def test_get_gitlab_commits_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    commits = [{"id": "abc123", "title": "Fix bug"}]
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse(commits),
    )

    result = get_gitlab_commits(
        config=_make_config(),
        project_id="my-org/my-repo",
        since="2024-01-01T00:00:00Z",
    )

    assert result == commits


def test_get_gitlab_commits_url_encodes_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_request(method: str, url: str, **kw: Any) -> _FakeResponse:
        captured["url"] = url
        return _FakeResponse([])

    monkeypatch.setattr("app.integrations.gitlab.httpx.request", _fake_request)

    get_gitlab_commits(
        config=_make_config(),
        project_id="my-org/my-repo",
        since="2024-01-01T00:00:00Z",
    )

    assert "my-org%2Fmy-repo" in captured["url"]


def test_get_gitlab_commits_returns_empty_list_for_non_list_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse({"error": "not found"}),
    )

    result = get_gitlab_commits(
        config=_make_config(),
        project_id="my-org/my-repo",
        since="2024-01-01T00:00:00Z",
    )

    assert result == []


def test_get_gitlab_commits_includes_since_param_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_request(method: str, url: str, **kw: Any) -> _FakeResponse:
        captured["params"] = kw.get("params", [])
        return _FakeResponse([])

    monkeypatch.setattr("app.integrations.gitlab.httpx.request", _fake_request)

    get_gitlab_commits(
        config=_make_config(),
        project_id="proj",
        since="2024-06-01T00:00:00Z",
    )

    param_keys = [k for k, _ in captured["params"]]
    assert "since" in param_keys


# ---------------------------------------------------------------------------
# get_gitlab_mrs
# ---------------------------------------------------------------------------


def test_get_gitlab_mrs_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    mrs = [{"iid": 1, "title": "Add feature"}]
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse(mrs),
    )

    result = get_gitlab_mrs(
        config=_make_config(),
        project_id="my-org/my-repo",
        updated_after="2024-01-01T00:00:00Z",
    )

    assert result == mrs


def test_get_gitlab_mrs_url_encodes_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_request(method: str, url: str, **kw: Any) -> _FakeResponse:
        captured["url"] = url
        return _FakeResponse([])

    monkeypatch.setattr("app.integrations.gitlab.httpx.request", _fake_request)

    get_gitlab_mrs(
        config=_make_config(),
        project_id="my-org/my-repo",
        updated_after="2024-01-01T00:00:00Z",
    )

    assert "my-org%2Fmy-repo" in captured["url"]


def test_get_gitlab_mrs_returns_empty_list_for_non_list_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse({}),
    )

    result = get_gitlab_mrs(
        config=_make_config(),
        project_id="proj",
        updated_after="2024-01-01T00:00:00Z",
    )

    assert result == []


# ---------------------------------------------------------------------------
# get_gitlab_pipelines
# ---------------------------------------------------------------------------


def test_get_gitlab_pipelines_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    pipelines = [{"id": 99, "status": "failed"}]
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse(pipelines),
    )

    result = get_gitlab_pipelines(
        config=_make_config(),
        project_id="my-org/my-repo",
        updated_after="2024-01-01T00:00:00Z",
    )

    assert result == pipelines


def test_get_gitlab_pipelines_url_encodes_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_request(method: str, url: str, **kw: Any) -> _FakeResponse:
        captured["url"] = url
        return _FakeResponse([])

    monkeypatch.setattr("app.integrations.gitlab.httpx.request", _fake_request)

    get_gitlab_pipelines(
        config=_make_config(),
        project_id="my-org/my-repo",
        updated_after="2024-01-01T00:00:00Z",
    )

    assert "my-org%2Fmy-repo" in captured["url"]


def test_get_gitlab_pipelines_returns_empty_list_for_non_list_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse({"unexpected": "dict"}),
    )

    result = get_gitlab_pipelines(
        config=_make_config(),
        project_id="proj",
        updated_after="2024-01-01T00:00:00Z",
    )

    assert result == []


# ---------------------------------------------------------------------------
# get_gitlab_file
# ---------------------------------------------------------------------------


def test_get_gitlab_file_returns_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    file_data = {"file_name": "README.md", "content": "SGVsbG8="}
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse(file_data),
    )

    result = get_gitlab_file(
        config=_make_config(),
        project_id="my-org/my-repo",
        file_path="README.md",
    )

    assert result == file_data


def test_get_gitlab_file_url_encodes_project_id_and_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_request(method: str, url: str, **kw: Any) -> _FakeResponse:
        captured["url"] = url
        return _FakeResponse({})

    monkeypatch.setattr("app.integrations.gitlab.httpx.request", _fake_request)

    get_gitlab_file(
        config=_make_config(),
        project_id="my-org/my-repo",
        file_path="src/main.py",
    )

    assert "my-org%2Fmy-repo" in captured["url"]
    assert "src%2Fmain.py" in captured["url"]


def test_get_gitlab_file_returns_empty_dict_for_non_dict_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.gitlab.httpx.request",
        lambda *a, **kw: _FakeResponse(["not", "a", "dict"]),
    )

    result = get_gitlab_file(
        config=_make_config(),
        project_id="proj",
        file_path="foo.py",
    )

    assert result == {}
