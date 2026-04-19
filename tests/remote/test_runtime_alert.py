"""Tests for build_runtime_alert_payload()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.cli.errors import OpenSREError
from app.deployment.health import HealthPollStatus
from app.remote.ops import RemoteOpsError, ServiceStatus
from app.remote.runtime_alert import build_runtime_alert_payload


def _status(**overrides: object) -> ServiceStatus:
    defaults: dict[str, object] = {
        "provider": "railway",
        "project": "proj-a",
        "service": "svc-a",
        "deployment_id": "dep-123",
        "deployment_status": "SUCCESS",
        "environment": "production",
        "url": "https://svc-a.up.railway.app",
        "health": "healthy",
        "metadata": {"region": "us-east"},
    }
    defaults.update(overrides)
    return ServiceStatus(**defaults)  # type: ignore[arg-type]


def _patch_registry(service_name: str, *, configured: bool = True) -> object:
    remotes = {service_name: "https://svc-a.up.railway.app"} if configured else {}
    return patch(
        "app.remote.runtime_alert.load_named_remotes",
        return_value=remotes,
    )


def _patch_ops_config(
    *, provider: str = "railway", project: str = "proj-a", service: str = "svc-a"
) -> object:
    return patch(
        "app.remote.runtime_alert.load_remote_ops_config",
        return_value={"provider": provider, "project": project, "service": service},
    )


def _patch_provider(provider_mock: MagicMock) -> object:
    return patch(
        "app.remote.runtime_alert.resolve_remote_ops_provider",
        return_value=provider_mock,
    )


def test_happy_path_returns_payload_with_service_logs_and_health() -> None:
    provider_mock = MagicMock()
    provider_mock.status.return_value = _status()
    provider_mock.fetch_logs.return_value = "log line 1\nlog line 2"

    health_result = HealthPollStatus(
        url="https://svc-a.up.railway.app/health",
        attempts=1,
        status_code=200,
        elapsed_seconds=0.25,
    )

    with (
        _patch_registry("my-svc"),
        _patch_ops_config(),
        _patch_provider(provider_mock),
        patch(
            "app.remote.runtime_alert.poll_deployment_health",
            return_value=health_result,
        ),
    ):
        payload = build_runtime_alert_payload("my-svc")

    assert payload["alert_name"] == "Remote runtime investigation: my-svc"
    assert payload["pipeline_name"] == "svc-a"
    assert payload["severity"] == "warning"
    assert "alert_source" not in payload
    assert payload["investigation_origin"] == "remote_runtime"
    assert payload["service"]["provider"] == "railway"
    assert payload["service"]["deployment_id"] == "dep-123"
    assert payload["service"]["health"] == "healthy"
    assert payload["recent_logs"] == "log line 1\nlog line 2"
    assert payload["health_probe"]["status_code"] == 200
    assert payload["health_probe"]["attempts"] == 1


def test_missing_service_name_raises() -> None:
    with pytest.raises(OpenSREError, match="Service name is required"):
        build_runtime_alert_payload("")


def test_unknown_service_raises_with_suggestion() -> None:
    with (
        _patch_registry("other-svc"),
        pytest.raises(OpenSREError, match="No remote named 'my-svc'"),
    ):
        build_runtime_alert_payload("my-svc")


def test_status_failure_raises_with_suggestion() -> None:
    provider_mock = MagicMock()
    provider_mock.status.side_effect = RemoteOpsError("railway down")

    with (
        _patch_registry("my-svc"),
        _patch_ops_config(),
        _patch_provider(provider_mock),
        pytest.raises(OpenSREError, match="Failed to fetch deployment status"),
    ):
        build_runtime_alert_payload("my-svc")


def test_logs_unavailable_is_graceful() -> None:
    provider_mock = MagicMock()
    provider_mock.status.return_value = _status()
    provider_mock.fetch_logs.side_effect = RemoteOpsError("no deployment")

    with (
        _patch_registry("my-svc"),
        _patch_ops_config(),
        _patch_provider(provider_mock),
        patch(
            "app.remote.runtime_alert.poll_deployment_health",
            return_value=HealthPollStatus(
                url="https://svc-a.up.railway.app/health",
                attempts=1,
                status_code=200,
                elapsed_seconds=0.1,
            ),
        ),
    ):
        payload = build_runtime_alert_payload("my-svc")

    assert "logs unavailable" in payload["recent_logs"]
    assert payload["health_probe"]["status_code"] == 200


def test_health_timeout_is_graceful() -> None:
    provider_mock = MagicMock()
    provider_mock.status.return_value = _status()
    provider_mock.fetch_logs.return_value = "some logs"

    with (
        _patch_registry("my-svc"),
        _patch_ops_config(),
        _patch_provider(provider_mock),
        patch(
            "app.remote.runtime_alert.poll_deployment_health",
            side_effect=TimeoutError("Deployment health check timed out"),
        ),
    ):
        payload = build_runtime_alert_payload("my-svc")

    assert payload["recent_logs"] == "some logs"
    assert "timed out" in payload["health_probe"]["error"]


def test_unsupported_provider_raises_friendly_error() -> None:
    with (
        _patch_registry("my-svc"),
        patch(
            "app.remote.runtime_alert.load_remote_ops_config",
            return_value={"provider": "unknown-provider", "project": None, "service": None},
        ),
        patch(
            "app.remote.runtime_alert.resolve_remote_ops_provider",
            side_effect=RemoteOpsError("Unsupported remote ops provider: unknown-provider"),
        ),
        pytest.raises(OpenSREError, match="Unsupported remote ops provider"),
    ):
        build_runtime_alert_payload("my-svc")


def test_missing_status_url_skips_health_probe() -> None:
    provider_mock = MagicMock()
    provider_mock.status.return_value = _status(url=None)
    provider_mock.fetch_logs.return_value = ""

    with (
        _patch_registry("my-svc"),
        _patch_ops_config(),
        _patch_provider(provider_mock),
    ):
        payload = build_runtime_alert_payload("my-svc")

    assert payload["health_probe"] == {"error": "no service URL available"}


def test_slack_thread_included_when_ref_and_token_provided() -> None:
    provider_mock = MagicMock()
    provider_mock.status.return_value = _status()
    provider_mock.fetch_logs.return_value = "logs"

    slack_payload = {
        "channel": "C01234",
        "ts": "1712345.000001",
        "messages": [{"user": "U1", "text": "help", "ts": "1712345.000001", "reactions": []}],
        "count": 1,
    }

    with (
        _patch_registry("my-svc"),
        _patch_ops_config(),
        _patch_provider(provider_mock),
        patch(
            "app.remote.runtime_alert.poll_deployment_health",
            return_value=HealthPollStatus(
                url="https://svc-a.up.railway.app/health",
                attempts=1,
                status_code=200,
                elapsed_seconds=0.1,
            ),
        ),
        patch(
            "app.remote.runtime_alert.fetch_slack_thread",
            return_value=slack_payload,
        ),
    ):
        payload = build_runtime_alert_payload(
            "my-svc",
            slack_thread_ref="C01234/1712345.000001",
            slack_bot_token="xoxb-fake",
        )

    assert payload["slack_thread"]["count"] == 1
    assert payload["slack_thread"]["channel"] == "C01234"


def test_slack_thread_captures_parse_error() -> None:
    provider_mock = MagicMock()
    provider_mock.status.return_value = _status()
    provider_mock.fetch_logs.return_value = ""

    with (
        _patch_registry("my-svc"),
        _patch_ops_config(),
        _patch_provider(provider_mock),
        patch(
            "app.remote.runtime_alert.poll_deployment_health",
            return_value=HealthPollStatus(
                url="https://svc-a.up.railway.app/health",
                attempts=1,
                status_code=200,
                elapsed_seconds=0.1,
            ),
        ),
    ):
        payload = build_runtime_alert_payload(
            "my-svc",
            slack_thread_ref="malformed-ref",
            slack_bot_token="xoxb-fake",
        )

    assert "error" in payload["slack_thread"]
    assert "CHANNEL/TS" in payload["slack_thread"]["error"]


def test_slack_thread_not_included_when_ref_absent() -> None:
    provider_mock = MagicMock()
    provider_mock.status.return_value = _status()
    provider_mock.fetch_logs.return_value = ""

    with (
        _patch_registry("my-svc"),
        _patch_ops_config(),
        _patch_provider(provider_mock),
        patch(
            "app.remote.runtime_alert.poll_deployment_health",
            return_value=HealthPollStatus(
                url="https://svc-a.up.railway.app/health",
                attempts=1,
                status_code=200,
                elapsed_seconds=0.1,
            ),
        ),
    ):
        payload = build_runtime_alert_payload("my-svc")

    assert "slack_thread" not in payload
