from __future__ import annotations

import httpx
import pytest

from integrations.posthog import (
    PostHogConfig,
    build_posthog_config,
    posthog_config_from_env,
    validate_posthog_config,
)
from integrations.posthog.classify import classify
from integrations.posthog.verifier import verify_posthog


def test_build_posthog_config_defaults() -> None:
    config = build_posthog_config({})

    assert config.base_url == "https://us.posthog.com"
    assert config.project_id == ""
    assert config.personal_api_key == ""
    assert config.timeout_seconds == 15.0


def test_posthog_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTHOG_PROJECT_ID", "123")
    monkeypatch.setenv("POSTHOG_PERSONAL_API_KEY", "phx_test")
    monkeypatch.setenv("POSTHOG_BASE_URL", "https://eu.posthog.com")
    monkeypatch.setenv("POSTHOG_TIMEOUT_SECONDS", "20")

    config = posthog_config_from_env()

    assert config is not None
    assert config.project_id == "123"
    assert config.personal_api_key == "phx_test"
    assert config.base_url == "https://eu.posthog.com"
    assert config.timeout_seconds == 20.0


def test_posthog_config_from_env_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTHOG_PROJECT_ID", raising=False)
    monkeypatch.delenv("POSTHOG_PERSONAL_API_KEY", raising=False)

    assert posthog_config_from_env() is None


def test_validate_posthog_config_success(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="phx_test",
    )

    def fake_request_json(*args, **kwargs):
        return {"id": 123, "name": "Demo Project"}

    monkeypatch.setattr("integrations.posthog.client._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is True
    assert "validated" in result.detail.lower()


def test_validate_posthog_config_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="bad_key",
    )

    request = httpx.Request("GET", "https://us.posthog.com/api/projects/123/")
    response = httpx.Response(401, request=request)

    def fake_request_json(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "Client error '401 Unauthorized' for url 'https://us.posthog.com/api/projects/123/'",
            request=request,
            response=response,
        )

    monkeypatch.setattr("integrations.posthog.client._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "HTTP 401" in result.detail


def test_validate_posthog_config_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="restricted_key",
    )

    request = httpx.Request("GET", "https://us.posthog.com/api/projects/123/")
    response = httpx.Response(
        403, text='{"detail": "You do not have permission."}', request=request
    )

    def fake_request_json(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "Client error '403 Forbidden'",
            request=request,
            response=response,
        )

    monkeypatch.setattr("integrations.posthog.client._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "HTTP 403" in result.detail
    assert "permission" in result.detail.lower()


def test_validate_posthog_config_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="999",
        personal_api_key="phx_test",
    )

    request = httpx.Request("GET", "https://us.posthog.com/api/projects/999/")
    response = httpx.Response(404, text='{"detail": "Not found."}', request=request)

    def fake_request_json(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "Client error '404 Not Found'",
            request=request,
            response=response,
        )

    monkeypatch.setattr("integrations.posthog.client._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "HTTP 404" in result.detail


def test_validate_posthog_config_http_error_detail_starts_with_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detail always starts with 'HTTP <status_code>' for HTTPStatusError."""
    config = PostHogConfig(project_id="123", personal_api_key="phx_test")
    request = httpx.Request("GET", "https://us.posthog.com/api/projects/123/")
    response = httpx.Response(401, request=request)

    monkeypatch.setattr(
        "integrations.posthog.client._request_json",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            httpx.HTTPStatusError("401", request=request, response=response)
        ),
    )

    result = validate_posthog_config(config)

    assert result.ok is False
    assert result.detail.startswith("HTTP ")


def test_validate_posthog_config_generic_error_still_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-HTTP exceptions are still caught and returned as plain error detail."""
    config = PostHogConfig(project_id="123", personal_api_key="phx_test")

    def fake_request_json(*args, **kwargs):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr("integrations.posthog.client._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "network unreachable" in result.detail


def test_classify_posthog_requires_project_and_key() -> None:
    cfg, service = classify({"project_id": "123"}, "env-posthog")
    assert cfg is None
    assert service is None

    cfg, service = classify(
        {"project_id": "123", "personal_api_key": "phx_test"},
        "env-posthog",
    )
    assert service == "posthog"
    assert cfg is not None
    assert cfg.project_id == "123"
    assert cfg.personal_api_key == "phx_test"


def test_verify_posthog_missing_project_id() -> None:
    result = verify_posthog("local env", {"personal_api_key": "phx_test"})
    assert result["service"] == "posthog"
    assert result["status"] == "failed"
    assert "project ID is required" in result["detail"]


def test_verify_posthog_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request_json(
        _config: PostHogConfig,
        _method: str,
        _path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict[str, int]:
        return {"id": 123, "name": "Demo Project"}

    monkeypatch.setattr("integrations.posthog.client._request_json", fake_request_json)

    result = verify_posthog(
        "local env",
        {
            "project_id": "123",
            "personal_api_key": "phx_test",
        },
    )
    assert result["service"] == "posthog"
    assert result["status"] == "passed"
    assert result["detail"] == "PostHog validated."
