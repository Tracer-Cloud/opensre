from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.posthog import (
    BounceRateAlert,
    BounceRateResult,
    PostHogConfig,
    check_bounce_rate_alert,
    posthog_config_from_env,
    query_bounce_rate,
    validate_posthog_config,
)


def test_posthog_config_from_env_loads_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTHOG_BASE_URL", "https://us.i.posthog.com")
    monkeypatch.setenv("POSTHOG_PROJECT_ID", "12345")
    monkeypatch.setenv("POSTHOG_PERSONAL_API_KEY", "phx_test")
    monkeypatch.setenv("POSTHOG_BOUNCE_THRESHOLD", "0.7")
    monkeypatch.setenv("POSTHOG_BOUNCE_WINDOW", "48h")

    config = posthog_config_from_env()

    assert config is not None
    assert config.base_url == "https://us.i.posthog.com"
    assert config.project_id == "12345"
    assert config.personal_api_key == "phx_test"
    assert config.bounce_rate_threshold == 0.7
    assert config.bounce_rate_window == "48h"


def test_posthog_config_from_env_returns_none_when_missing_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POSTHOG_PROJECT_ID", raising=False)
    monkeypatch.delenv("POSTHOG_PERSONAL_API_KEY", raising=False)

    config = posthog_config_from_env()

    assert config is None


def test_validate_posthog_config_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        base_url="https://us.i.posthog.com",
        project_id="123",
        personal_api_key="phx_test",
    )

    def mock_request(*args, **kwargs):
        return httpx.Response(
            200,
            json={"id": 123, "name": "Demo Project"},
            request=httpx.Request("GET", "https://us.i.posthog.com/api/projects/123/"),
        )

    monkeypatch.setattr("app.integrations.posthog.httpx.request", mock_request)

    result = validate_posthog_config(config)

    assert result.ok is True
    assert "validated" in result.detail.lower()


def test_validate_posthog_config_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        base_url="https://us.i.posthog.com",
        project_id="123",
        personal_api_key="bad_key",
    )

    def mock_request(*args, **kwargs):
        return httpx.Response(
            401,
            text="Unauthorized",
            request=httpx.Request("GET", "https://us.i.posthog.com/api/projects/123/"),
        )

    monkeypatch.setattr("app.integrations.posthog.httpx.request", mock_request)

    result = validate_posthog_config(config)

    assert result.ok is False
    assert "failed" in result.detail.lower()


def test_query_bounce_rate_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        base_url="https://us.i.posthog.com",
        project_id="123",
        personal_api_key="phx_test",
    )

    def mock_request(*args, **kwargs):
        return httpx.Response(
            200,
            json={"total_sessions": 1000, "bounced_sessions": 750},
            request=httpx.Request(
                "GET",
                "https://us.i.posthog.com/api/projects/123/insights/trend/",
            ),
        )

    monkeypatch.setattr("app.integrations.posthog.httpx.request", mock_request)

    result = query_bounce_rate(config, period="24h")

    assert isinstance(result, BounceRateResult)
    assert result.total_sessions == 1000
    assert result.bounced_sessions == 750
    assert result.bounce_rate == 0.75
    assert result.period == "24h"
    assert isinstance(result.queried_at, datetime)
    assert result.queried_at.tzinfo == timezone.utc


def test_check_bounce_rate_alert_returns_none_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        base_url="https://us.i.posthog.com",
        project_id="123",
        personal_api_key="phx_test",
        bounce_rate_threshold=0.6,
        bounce_rate_window="24h",
    )

    monkeypatch.setattr(
        "app.integrations.posthog.query_bounce_rate",
        lambda config, period: BounceRateResult(
            bounce_rate=0.3,
            total_sessions=600,
            bounced_sessions=180,
            period=period,
            queried_at=datetime.now(timezone.utc),
        ),
    )

    alert = check_bounce_rate_alert(config)

    assert alert is None


def test_check_bounce_rate_alert_returns_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        base_url="https://us.i.posthog.com",
        project_id="123",
        personal_api_key="phx_test",
        bounce_rate_threshold=0.6,
        bounce_rate_window="24h",
    )

    monkeypatch.setattr(
        "app.integrations.posthog.query_bounce_rate",
        lambda config, period: BounceRateResult(
            bounce_rate=0.75,
            total_sessions=1000,
            bounced_sessions=750,
            period=period,
            queried_at=datetime.now(timezone.utc),
        ),
    )

    alert = check_bounce_rate_alert(config)

    assert isinstance(alert, BounceRateAlert)
    assert alert.severity == "warning"
    assert alert.bounce_rate == 0.75
    assert "75.0%" in alert.message


def test_check_bounce_rate_alert_returns_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        base_url="https://us.i.posthog.com",
        project_id="123",
        personal_api_key="phx_test",
        bounce_rate_threshold=0.6,
        bounce_rate_window="24h",
    )

    monkeypatch.setattr(
        "app.integrations.posthog.query_bounce_rate",
        lambda config, period: BounceRateResult(
            bounce_rate=0.95,
            total_sessions=1000,
            bounced_sessions=950,
            period=period,
            queried_at=datetime.now(timezone.utc),
        ),
    )

    alert = check_bounce_rate_alert(config)

    assert isinstance(alert, BounceRateAlert)
    assert alert.severity == "critical"
    assert alert.bounce_rate == 0.95


def test_query_bounce_rate_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PostHogConfig(
        base_url="https://us.i.posthog.com",
        project_id="123",
        personal_api_key="phx_test",
    )

    def mock_request(*args, **kwargs):
        response = httpx.Response(
            500,
            text="server error",
            request=httpx.Request(
                "GET",
                "https://us.i.posthog.com/api/projects/123/insights/trend/",
            ),
        )
        raise httpx.HTTPStatusError("server error", request=response.request, response=response)

    monkeypatch.setattr("app.integrations.posthog.httpx.request", mock_request)

    with pytest.raises(httpx.HTTPStatusError):
        query_bounce_rate(config, period="24h")
