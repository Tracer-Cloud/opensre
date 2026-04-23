from __future__ import annotations

from typing import Any

import pytest

from app.integrations.verify import (
    _verify_aws,
    _verify_coralogix,
    _verify_datadog,
    _verify_github,
    _verify_grafana,
    _verify_honeycomb,
    _verify_sentry,
    _verify_snowflake,
    _verify_tracer,
    _verify_vercel,
    resolve_effective_integrations,
    verification_exit_code,
    verify_integrations,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def test_resolve_effective_integrations_prefers_local_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
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


def test_resolve_effective_integrations_includes_honeycomb_and_coralogix_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("HONEYCOMB_API_KEY", "hny_test")
    monkeypatch.setenv("HONEYCOMB_DATASET", "prod-api")
    monkeypatch.setenv("CORALOGIX_API_KEY", "cx_test")
    monkeypatch.setenv("CORALOGIX_APPLICATION_NAME", "payments")
    monkeypatch.setenv("CORALOGIX_SUBSYSTEM_NAME", "worker")

    effective = resolve_effective_integrations()

    assert effective["honeycomb"]["config"]["dataset"] == "prod-api"
    assert effective["coralogix"]["config"]["application_name"] == "payments"


def test_resolve_effective_integrations_skips_snowflake_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "env-account")
    monkeypatch.delenv("SNOWFLAKE_TOKEN", raising=False)
    monkeypatch.setenv("SNOWFLAKE_USER", "service-user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")

    effective = resolve_effective_integrations()

    assert "snowflake" not in effective


def test_resolve_effective_integrations_keeps_incomplete_datadog_store_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "datadog-local",
                "service": "datadog",
                "status": "active",
                "credentials": {
                    "api_key": "",
                    "app_key": "",
                    "site": "datadoghq.com",
                },
            }
        ],
    )

    effective = resolve_effective_integrations()

    assert effective["datadog"]["source"] == "local store"
    assert effective["datadog"]["config"]["integration_id"] == "datadog-local"
    assert effective["datadog"]["config"]["api_key"] == ""


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


def test_verify_datadog_accepts_integration_id() -> None:
    result = _verify_datadog(
        "local store",
        {
            "api_key": "",
            "app_key": "",
            "site": "datadoghq.com",
            "integration_id": "datadog-local",
        },
    )

    assert result["status"] == "missing"
    assert "Missing API key" in result["detail"]


def test_verify_snowflake_requires_token() -> None:
    result = _verify_snowflake(
        "local env",
        {
            "account_identifier": "xy12345.us-east-1",
            "user": "service-user",
            "password": "secret",
            "token": "",
        },
    )

    assert result["status"] == "missing"
    assert result["detail"] == "Missing token credentials."


def test_verify_honeycomb_uses_auth_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.HoneycombClient.validate_access",
        lambda _self: {"success": True, "environment": {"slug": "prod"}},
    )
    monkeypatch.setattr(
        "app.integrations.verify.HoneycombClient.run_query",
        lambda _self, *_args, **_kwargs: {"success": True, "results": [{}]},
    )

    result = _verify_honeycomb(
        "local env",
        {"api_key": "hny_test", "dataset": "prod-api", "base_url": "https://api.honeycomb.io"},
    )

    assert result["status"] == "passed"
    assert "prod-api" in result["detail"]


def test_verify_coralogix_reports_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.CoralogixClient.validate_access",
        lambda _self: {"success": False, "error": "HTTP 401: unauthorized"},
    )

    result = _verify_coralogix(
        "local env",
        {
            "api_key": "cx_test",
            "base_url": "https://api.coralogix.com",
            "application_name": "payments",
            "subsystem_name": "worker",
        },
    )

    assert result["status"] == "failed"
    assert "401" in result["detail"]


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


def test_verify_github_uses_shared_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.validate_github_mcp_config",
        lambda _config: type("Result", (), {"ok": True, "detail": "GitHub MCP ok"})(),
    )

    result = _verify_github(
        "local env",
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp",
        },
    )

    assert result["status"] == "passed"
    assert result["detail"] == "GitHub MCP ok"


def test_verify_sentry_uses_shared_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.validate_sentry_config",
        lambda _config: type("Result", (), {"ok": True, "detail": "Sentry ok"})(),
    )

    result = _verify_sentry(
        "local env",
        {
            "base_url": "https://sentry.io",
            "organization_slug": "demo-org",
            "auth_token": "sntrys",
            "project_slug": "payments",
        },
    )

    assert result["status"] == "passed"
    assert result["detail"] == "Sentry ok"


def test_verification_exit_code_requires_core_success() -> None:
    assert (
        verification_exit_code(
            [
                {
                    "service": "slack",
                    "source": "local env",
                    "status": "configured",
                    "detail": "Incoming webhook configured.",
                }
            ]
        )
        == 1
    )

    assert (
        verification_exit_code(
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
        )
        == 0
    )

    assert (
        verification_exit_code(
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
        )
        == 1
    )

    assert (
        verification_exit_code(
            [
                {
                    "service": "slack",
                    "source": "local env",
                    "status": "configured",
                    "detail": "Incoming webhook configured.",
                }
            ],
            requested_service="slack",
        )
        == 0
    )


def test_verify_vercel_passes_with_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeVercelClient:
        def __init__(self, config: Any) -> None:
            pass

        def __enter__(self) -> _FakeVercelClient:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def list_projects(self) -> dict[str, Any]:
            return {"success": True, "projects": [{"id": "p1"}, {"id": "p2"}], "total": 2}

    monkeypatch.setattr("app.integrations.verify.VercelClient", _FakeVercelClient)

    result = _verify_vercel("local env", {"api_token": "tok_test", "team_id": ""})

    assert result["status"] == "passed"
    assert "2 project" in result["detail"]


def test_verify_vercel_fails_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeVercelClient:
        def __init__(self, config: Any) -> None:
            pass

        def __enter__(self) -> _FakeVercelClient:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def list_projects(self) -> dict[str, Any]:
            return {"success": False, "error": "HTTP 401: unauthorized"}

    monkeypatch.setattr("app.integrations.verify.VercelClient", _FakeVercelClient)

    result = _verify_vercel("local env", {"api_token": "bad_token", "team_id": ""})

    assert result["status"] == "failed"
    assert "401" in result["detail"]


def test_verify_vercel_missing_token() -> None:
    result = _verify_vercel("local env", {"api_token": "", "team_id": ""})
    assert result["status"] == "missing"


def test_verify_integrations_dispatches_to_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeVercelClient:
        def __init__(self, config: Any) -> None:
            pass

        def __enter__(self) -> _FakeVercelClient:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def list_projects(self) -> dict[str, Any]:
            return {"success": True, "projects": [], "total": 0}

    monkeypatch.setattr("app.integrations.verify.VercelClient", _FakeVercelClient)
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "vercel-1",
                "service": "vercel",
                "status": "active",
                "credentials": {"api_token": "tok_test", "team_id": ""},
            }
        ],
    )

    results = verify_integrations("vercel")

    assert len(results) == 1
    assert results[0]["service"] == "vercel"
    assert results[0]["status"] == "passed"


def test_resolve_effective_integrations_includes_vercel_from_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "vercel-store-1",
                "service": "vercel",
                "status": "active",
                "credentials": {"api_token": "tok_store", "team_id": "team_xyz"},
            }
        ],
    )

    effective = resolve_effective_integrations()

    vercel = effective.get("vercel")
    assert vercel is not None
    assert vercel["config"]["api_token"] == "tok_store"
    assert vercel["config"]["team_id"] == "team_xyz"
    assert vercel["source"] == "local store"


def test_resolve_effective_integrations_includes_vercel_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("VERCEL_API_TOKEN", "tok_env")
    monkeypatch.setenv("VERCEL_TEAM_ID", "team_env")

    effective = resolve_effective_integrations()

    vercel = effective.get("vercel")
    assert vercel is not None
    assert vercel["config"]["api_token"] == "tok_env"
    assert vercel["source"] == "local env"
