from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.integrations.posthog import (
    BounceRateAlert,
    BounceRateResult,
    PostHogConfig,
    build_posthog_config,
    check_bounce_rate_alert,
    posthog_config_from_env,
    query_bounce_rate,
    validate_posthog_config,
)


def test_build_posthog_config_defaults() -> None:
    config = build_posthog_config({})

    assert config.base_url == "https://us.i.posthog.com"
    assert config.project_id == ""
    assert config.personal_api_key == ""
    assert config.timeout_seconds == 15.0
    assert config.bounce_rate_threshold == 0.6
    assert config.bounce_rate_window == "24h"


def test_posthog_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTHOG_PROJECT_ID", "123")
    monkeypatch.setenv("POSTHOG_PERSONAL_API_KEY", "phx_test")
    monkeypatch.setenv("POSTHOG_BASE_URL", "https://eu.i.posthog.com")
    monkeypatch.setenv("POSTHOG_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("POSTHOG_BOUNCE_THRESHOLD", "0.75")
    monkeypatch.setenv("POSTHOG_BOUNCE_WINDOW", "48h")

    config = posthog_config_from_env()

    assert config is not None
    assert config.project_id == "123"
    assert config.personal_api_key == "phx_test"
    assert config.base_url == "https://eu.i.posthog.com"
    assert config.timeout_seconds == 20.0
    assert config.bounce_rate_threshold == 0.75
    assert config.bounce_rate_window == "48h"


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

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is True
    assert "validated" in result.detail.lower()


def test_validate_posthog_config_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="bad_key",
    )

    request = httpx.Request("GET", "https://us.i.posthog.com/api/projects/123/")
    response = httpx.Response(401, request=request)

    def fake_request_json(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "Client error '401 Unauthorized' for url 'https://us.i.posthog.com/api/projects/123/'",
            request=request,
            response=response,
        )

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "HTTP 401" in result.detail


def test_validate_posthog_config_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="restricted_key",
    )

    request = httpx.Request("GET", "https://us.i.posthog.com/api/projects/123/")
    response = httpx.Response(
        403, text='{"detail": "You do not have permission."}', request=request
    )

    def fake_request_json(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "Client error '403 Forbidden'",
            request=request,
            response=response,
        )

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "HTTP 403" in result.detail
    assert "permission" in result.detail.lower()


def test_validate_posthog_config_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="999",
        personal_api_key="phx_test",
    )

    request = httpx.Request("GET", "https://us.i.posthog.com/api/projects/999/")
    response = httpx.Response(404, text='{"detail": "Not found."}', request=request)

    def fake_request_json(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "Client error '404 Not Found'",
            request=request,
            response=response,
        )

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "HTTP 404" in result.detail


def test_validate_posthog_config_http_error_detail_starts_with_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detail always starts with 'HTTP <status_code>' for HTTPStatusError."""
    config = PostHogConfig(project_id="123", personal_api_key="phx_test")
    request = httpx.Request("GET", "https://us.i.posthog.com/api/projects/123/")
    response = httpx.Response(401, request=request)

    monkeypatch.setattr(
        "app.integrations.posthog._request_json",
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

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "network unreachable" in result.detail


def test_query_bounce_rate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="phx_test",
    )

    def fake_request_json(*args, **kwargs):
        return {"results": [[750, 1000]]}

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    result = query_bounce_rate(config, period="24h")

    assert result.bounce_rate == 0.75
    assert result.total_sessions == 1000
    assert result.bounced_sessions == 750
    assert result.period == "24h"
    assert isinstance(result.queried_at, datetime)
    assert result.queried_at.tzinfo == UTC


def test_query_bounce_rate_clamps_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="phx_test",
    )

    def fake_request_json(*args, **kwargs):
        return {"results": [[1500, 1000]]}

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    result = query_bounce_rate(config, period="24h")

    assert result.bounce_rate == 1.0
    assert result.total_sessions == 1000
    assert result.bounced_sessions == 1500


def test_query_bounce_rate_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="phx_test",
    )

    def fake_request_json(*args, **kwargs):
        return {"results": []}

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    with pytest.raises(ValueError, match="Empty PostHog response"):
        query_bounce_rate(config)


def test_query_bounce_rate_invalid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="phx_test",
    )

    def fake_request_json(*args, **kwargs):
        return []

    monkeypatch.setattr("app.integrations.posthog._request_json", fake_request_json)

    with pytest.raises(ValueError, match="Unexpected PostHog response"):
        query_bounce_rate(config)


def test_check_bounce_rate_alert_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="phx_test",
        bounce_rate_threshold=0.6,
        bounce_rate_window="24h",
    )

    monkeypatch.setattr(
        "app.integrations.posthog.query_bounce_rate",
        lambda _config, period: BounceRateResult(
            bounce_rate=0.3,
            total_sessions=600,
            bounced_sessions=180,
            period=period,
            queried_at=datetime.now(UTC),
        ),
    )

    alert = check_bounce_rate_alert(config)

    assert alert is None


def test_check_bounce_rate_alert_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="phx_test",
        bounce_rate_threshold=0.6,
        bounce_rate_window="24h",
    )

    monkeypatch.setattr(
        "app.integrations.posthog.query_bounce_rate",
        lambda _config, period: BounceRateResult(
            bounce_rate=0.75,
            total_sessions=1000,
            bounced_sessions=750,
            period=period,
            queried_at=datetime.now(UTC),
        ),
    )

    alert = check_bounce_rate_alert(config)

    assert isinstance(alert, BounceRateAlert)
    assert alert.severity == "warning"
    assert alert.bounce_rate == 0.75
    assert alert.threshold == 0.6
    assert "75.0%" in alert.message


def test_check_bounce_rate_alert_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        project_id="123",
        personal_api_key="phx_test",
        bounce_rate_threshold=0.6,
        bounce_rate_window="24h",
    )

    monkeypatch.setattr(
        "app.integrations.posthog.query_bounce_rate",
        lambda _config, period: BounceRateResult(
            bounce_rate=0.95,
            total_sessions=1000,
            bounced_sessions=950,
            period=period,
            queried_at=datetime.now(UTC),
        ),
    )

    alert = check_bounce_rate_alert(config)

    assert isinstance(alert, BounceRateAlert)
    assert alert.severity == "critical"
    assert alert.bounce_rate == 0.95
    assert alert.total_sessions == 1000
    assert alert.bounced_sessions == 950
