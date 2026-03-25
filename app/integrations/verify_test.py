from __future__ import annotations

from typing import Any

import pytest

from app.integrations.verify import (
    _verify_aws,
    _verify_datadog,
    _verify_grafana,
    _verify_tracer,
    resolve_effective_integrations,
    verification_exit_code,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def test_resolve_effective_integrations_prefers_local_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.load_integrations",
        lambda: [
            {
                "id": "grafana-local",
                "service": "grafana",
                "status": "active",
                "credentials": {
                    "endpoint": "https://store.grafana.net",
                    "api_key": "store-token",
                },
            }
        ],
    )
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://env.grafana.net")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "env-token")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T000/B000/test")
    monkeypatch.setenv("JWT_TOKEN", "env-jwt")

    effective = resolve_effective_integrations()

    assert effective["grafana"]["source"] == "local store"
    assert effective["grafana"]["config"]["endpoint"] == "https://store.grafana.net"
    assert effective["slack"]["source"] == "local env"
    assert effective["tracer"]["source"] == "local env"


def test_verify_grafana_passes_with_supported_datasource(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_requests_get(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            [
                {"type": "loki", "uid": "logs", "name": "Logs"},
                {"type": "prometheus", "uid": "metrics", "name": "Metrics"},
            ]
        )

    monkeypatch.setattr(
        "app.integrations.verify.requests.get",
        _fake_requests_get,
    )

    result = _verify_grafana(
        "local env",
        {"endpoint": "https://example.grafana.net", "api_key": "token"},
    )

    assert result["status"] == "passed"
    assert "loki" in result["detail"]
    assert "prometheus" in result["detail"]


def test_verify_datadog_reports_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_list_monitors(_self: Any) -> dict[str, Any]:
        return {"success": False, "error": "HTTP 403: forbidden"}

    monkeypatch.setattr(
        "app.integrations.verify.DatadogClient.list_monitors",
        _fake_list_monitors,
    )

    result = _verify_datadog(
        "local env",
        {"api_key": "dd-api", "app_key": "dd-app", "site": "datadoghq.com"},
    )

    assert result["status"] == "failed"
    assert "403" in result["detail"]


def test_verify_aws_assume_role_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BaseSTSClient:
        def assume_role(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["RoleArn"] == "arn:aws:iam::123456789012:role/TracerReadOnly"
            assert kwargs["ExternalId"] == "external-123"
            return {
                "Credentials": {
                    "AccessKeyId": "ASIA_TEST",
                    "SecretAccessKey": "secret",
                    "SessionToken": "session",
                }
            }

    class _AssumedSTSClient:
        def get_caller_identity(self) -> dict[str, str]:
            return {
                "Account": "123456789012",
                "Arn": "arn:aws:sts::123456789012:assumed-role/TracerReadOnly/TracerIntegrationVerify",
            }

    def _fake_boto3_client(service_name: str, **kwargs: Any) -> Any:
        assert service_name == "sts"
        if kwargs.get("aws_access_key_id"):
            return _AssumedSTSClient()
        return _BaseSTSClient()

    monkeypatch.setattr("app.integrations.verify.boto3.client", _fake_boto3_client)

    result = _verify_aws(
        "local store",
        {
            "role_arn": "arn:aws:iam::123456789012:role/TracerReadOnly",
            "external_id": "external-123",
            "region": "us-east-1",
        },
    )

    assert result["status"] == "passed"
    assert "assume-role" in result["detail"]
    assert "123456789012" in result["detail"]


def test_verify_tracer_passes_with_env_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeTracerClient:
        def __init__(self, base_url: str, org_id: str, jwt_token: str) -> None:
            assert base_url == "https://app.tracer.cloud"
            assert org_id == "org_123"
            assert jwt_token == "jwt-token"

        def get_all_integrations(self) -> list[dict[str, str]]:
            return [{"id": "int-1"}, {"id": "int-2"}]

    monkeypatch.setattr("app.integrations.verify.extract_org_id_from_jwt", lambda _token: "org_123")
    monkeypatch.setattr("app.integrations.verify.TracerClient", _FakeTracerClient)

    result = _verify_tracer(
        "local env",
        {"base_url": "https://app.tracer.cloud", "jwt_token": "jwt-token"},
    )

    assert result["status"] == "passed"
    assert "org_123" in result["detail"]
    assert "2 integrations" in result["detail"]


def test_verification_exit_code_requires_core_success() -> None:
    assert verification_exit_code(
        [
            {
                "service": "slack",
                "source": "local env",
                "status": "configured",
                "detail": "Incoming webhook configured.",
            }
        ]
    ) == 1

    assert verification_exit_code(
        [
            {
                "service": "grafana",
                "source": "local env",
                "status": "passed",
                "detail": "Connected.",
            },
            {
                "service": "slack",
                "source": "local env",
                "status": "configured",
                "detail": "Incoming webhook configured.",
            },
        ]
    ) == 0

    assert verification_exit_code(
        [
            {
                "service": "grafana",
                "source": "local env",
                "status": "passed",
                "detail": "Connected.",
            },
            {
                "service": "slack",
                "source": "local env",
                "status": "failed",
                "detail": "Webhook post failed.",
            },
        ]
    ) == 1

    assert verification_exit_code(
        [
            {
                "service": "slack",
                "source": "local env",
                "status": "configured",
                "detail": "Incoming webhook configured.",
            }
        ],
        requested_service="slack",
    ) == 0
