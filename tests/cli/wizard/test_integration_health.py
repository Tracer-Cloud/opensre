from __future__ import annotations

import sys
import types

import pytest

from app.cli.wizard.integration_health import (
    validate_aws_integration,
    validate_betterstack_integration,
    validate_coralogix_integration,
    validate_datadog_integration,
    validate_discord_bot,
    validate_github_mcp_integration,
    validate_grafana_integration,
    validate_honeycomb_integration,
    validate_sentry_integration,
    validate_slack_webhook,
    validate_vercel_integration,
)
from app.integrations.betterstack import BetterStackValidationResult
from app.integrations.github_mcp import GitHubMCPValidationResult


class _FakeGrafanaClient:
    def __init__(self, discovered: dict[str, str]) -> None:
        self._discovered = discovered

    def discover_datasource_uids(self) -> dict[str, str]:
        return self._discovered


class _FakeDatadogClient:
    def __init__(self, result: dict[str, object]) -> None:
        self._result = result

    def list_monitors(self) -> dict[str, object]:
        return self._result


def test_validate_grafana_integration_succeeds_when_datasources_are_discovered(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.get_grafana_client_from_credentials",
        lambda **_kwargs: _FakeGrafanaClient({"loki_uid": "loki-1", "tempo_uid": "tempo-1"}),
    )

    result = validate_grafana_integration(endpoint="https://grafana.example.com", api_key="token")

    assert result.ok is True
    assert "datasource discovery" in result.detail


def test_validate_grafana_integration_fails_when_no_datasources_are_found(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.get_grafana_client_from_credentials",
        lambda **_kwargs: _FakeGrafanaClient({}),
    )

    result = validate_grafana_integration(endpoint="https://grafana.example.com", api_key="token")

    assert result.ok is False
    assert "no datasources" in result.detail.lower()


def test_validate_datadog_integration_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.DatadogClient",
        lambda _config: _FakeDatadogClient({"success": True, "total": 7}),
    )

    result = validate_datadog_integration(api_key="dd-api", app_key="dd-app", site="datadoghq.com")

    assert result.ok is True
    assert "fetched 7 monitors" in result.detail.lower()


def test_validate_datadog_integration_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.DatadogClient",
        lambda _config: _FakeDatadogClient({"success": False, "error": "HTTP 403"}),
    )

    result = validate_datadog_integration(api_key="dd-api", app_key="dd-app", site="datadoghq.com")

    assert result.ok is False
    assert "http 403" in result.detail.lower()


def test_validate_honeycomb_integration_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.HoneycombClient.validate_access",
        lambda _self: {"success": True, "environment": {"slug": "prod"}},
    )
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.HoneycombClient.run_query",
        lambda _self, *_args, **_kwargs: {"success": True, "results": [{}]},
    )

    result = validate_honeycomb_integration(
        api_key="hny_test",
        dataset="prod-api",
        base_url="https://api.honeycomb.io",
    )

    assert result.ok is True
    assert "dataset prod-api" in result.detail.lower()


def test_validate_coralogix_integration_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.CoralogixClient.validate_access",
        lambda _self: {"success": False, "error": "HTTP 401"},
    )

    result = validate_coralogix_integration(
        api_key="cx_test",
        base_url="https://api.coralogix.com",
        application_name="payments",
        subsystem_name="worker",
    )

    assert result.ok is False
    assert "http 401" in result.detail.lower()


def test_validate_slack_webhook_succeeds_with_non_posting_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.requests.get",
        lambda *_args, **_kwargs: types.SimpleNamespace(status_code=405),
    )

    result = validate_slack_webhook(webhook_url="https://hooks.slack.com/services/T000/B000/abc")

    assert result.ok is True
    assert "non-posting probe" in result.detail


def test_validate_slack_webhook_fails_for_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.requests.get",
        lambda *_args, **_kwargs: types.SimpleNamespace(status_code=404),
    )

    result = validate_slack_webhook(webhook_url="https://hooks.slack.com/services/T000/B000/abc")

    assert result.ok is False
    assert "404" in result.detail


def test_validate_slack_webhook_fails_for_invalid_host() -> None:
    result = validate_slack_webhook(webhook_url="https://example.com/services/T000/B000/abc")

    assert result.ok is False
    assert "slack domain" in result.detail.lower()


def test_validate_aws_integration_succeeds_with_static_credentials(monkeypatch) -> None:
    class _FakeSts:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/demo"}

    fake_boto3 = types.SimpleNamespace(client=lambda *_args, **_kwargs: _FakeSts())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    result = validate_aws_integration(
        region="us-east-1",
        access_key_id="AKIA...",
        secret_access_key="secret",
    )

    assert result.ok is True
    assert "123456789012" in result.detail


def test_validate_aws_integration_succeeds_with_role_assumption(monkeypatch) -> None:
    class _FakeBaseSts:
        def assume_role(self, **_kwargs) -> dict[str, dict[str, str]]:
            return {
                "Credentials": {
                    "AccessKeyId": "ASIA...",
                    "SecretAccessKey": "secret",
                    "SessionToken": "token",
                }
            }

    class _FakeAssumedSts:
        def get_caller_identity(self) -> dict[str, str]:
            return {
                "Account": "123456789012",
                "Arn": "arn:aws:sts::123456789012:assumed-role/demo/session",
            }

    def _client(service_name: str, **kwargs):
        if service_name != "sts":
            raise AssertionError("unexpected service")
        if "aws_access_key_id" in kwargs:
            return _FakeAssumedSts()
        return _FakeBaseSts()

    fake_boto3 = types.SimpleNamespace(client=_client)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    result = validate_aws_integration(
        region="us-east-1",
        role_arn="arn:aws:iam::123456789012:role/demo",
        external_id="external-id",
    )

    assert result.ok is True
    assert "assumed-role" in result.detail


def test_validate_aws_integration_fails_when_boto3_client_raises(monkeypatch) -> None:
    class _FailingSts:
        def get_caller_identity(self) -> dict[str, str]:
            raise RuntimeError("denied")

    fake_boto3 = types.SimpleNamespace(client=lambda *_args, **_kwargs: _FailingSts())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    result = validate_aws_integration(
        region="us-east-1",
        access_key_id="AKIA...",
        secret_access_key="secret",
        session_token="",
    )

    assert result.ok is False
    assert "denied" in result.detail.lower()


def test_validate_github_mcp_integration_uses_shared_validator(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.validate_github_mcp_config",
        lambda _config, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail="OK @ghuser; repos=1; owners=o; examples=o/r; mcp_tools=1",
            authenticated_user="ghuser",
            repo_access_count=1,
            repo_access_scope_owners=("o",),
            repo_access_samples=("o/r",),
        ),
    )

    result = validate_github_mcp_integration(
        url="https://api.githubcopilot.com/mcp/",
        mode="streamable-http",
        auth_token="ghp_test",
        toolsets=["repos"],
    )

    assert result.ok is True
    assert "Configuration validation: succeeded" in result.detail
    assert "GitHub identity: @ghuser" in result.detail
    assert "Repositories returned (probe): 1" in result.detail
    assert result.github_mcp is not None
    assert result.github_mcp.authenticated_user == "ghuser"


def test_validate_sentry_integration_uses_shared_validator(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.validate_sentry_config",
        lambda _config: types.SimpleNamespace(ok=True, detail="Sentry ok"),
    )

    result = validate_sentry_integration(
        base_url="https://sentry.io",
        organization_slug="demo-org",
        auth_token="sntrys_test",
        project_slug="payments",
    )

    assert result.ok is True
    assert result.detail == "Sentry ok"


class _FakeVercelClient:
    def __init__(self, result: dict) -> None:
        self._result = result

    def __enter__(self) -> _FakeVercelClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def list_projects(self) -> dict:
        return self._result


def test_validate_vercel_integration_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.VercelClient",
        lambda _config: _FakeVercelClient(
            {"success": True, "projects": [{"id": "p1"}], "total": 1}
        ),
    )

    result = validate_vercel_integration(api_token="tok_test")

    assert result.ok is True
    assert "1 project" in result.detail


def test_validate_vercel_integration_succeeds_with_team_id(monkeypatch) -> None:
    captured: dict = {}

    class _CapturingClient:
        def __init__(self, config) -> None:
            captured["team_id"] = config.team_id

        def __enter__(self) -> _CapturingClient:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def list_projects(self) -> dict:
            return {"success": True, "projects": [], "total": 0}

    monkeypatch.setattr("app.cli.wizard.integration_health.VercelClient", _CapturingClient)

    result = validate_vercel_integration(api_token="tok_test", team_id="team_xyz")

    assert result.ok is True
    assert captured["team_id"] == "team_xyz"


def test_validate_vercel_integration_fails_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.VercelClient",
        lambda _config: _FakeVercelClient({"success": False, "error": "HTTP 401: unauthorized"}),
    )

    result = validate_vercel_integration(api_token="bad_token")

    assert result.ok is False
    assert "401" in result.detail


def test_validate_vercel_integration_fails_with_empty_token() -> None:
    result = validate_vercel_integration(api_token="")

    assert result.ok is False
    assert "required" in result.detail.lower()


def test_validate_vercel_integration_surfaces_exception(monkeypatch) -> None:
    def _raise(_config):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr("app.cli.wizard.integration_health.VercelClient", _raise)

    result = validate_vercel_integration(api_token="tok_test")

    assert result.ok is False


# ---------------------------------------------------------------------------
# validate_discord_bot
# ---------------------------------------------------------------------------


def test_validate_discord_bot_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *_a, **_kw: types.SimpleNamespace(
            status_code=200,
            json=lambda: {"username": "my-sre-bot"},
        ),
    )
    result = validate_discord_bot(bot_token="Bot.valid.token")
    assert result.ok is True
    assert "my-sre-bot" in result.detail


def test_validate_discord_bot_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *_a, **_kw: types.SimpleNamespace(
            status_code=401,
            json=lambda: {"message": "401: Unauthorized"},
        ),
    )
    result = validate_discord_bot(bot_token="bad-token")
    assert result.ok is False
    assert "invalid or revoked" in result.detail.lower()


def test_validate_discord_bot_unexpected_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *_a, **_kw: types.SimpleNamespace(
            status_code=500,
            json=lambda: {},
        ),
    )
    result = validate_discord_bot(bot_token="some-token")
    assert result.ok is False
    assert "500" in result.detail


def test_validate_discord_bot_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx as _httpx

    def _raise(*_a: object, **_kw: object) -> None:
        raise _httpx.RequestError("connection refused")

    monkeypatch.setattr("httpx.get", _raise)
    result = validate_discord_bot(bot_token="some-token")
    assert result.ok is False
    assert "unreachable" in result.detail.lower()


def test_validate_betterstack_integration_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.validate_betterstack_config",
        lambda _config: BetterStackValidationResult(ok=True, detail="Connected."),
    )
    result = validate_betterstack_integration(
        query_endpoint="https://eu-nbg-2-connect.betterstackdata.com",
        username="u",
        password="p",
        sources=["t1_myapp"],
    )
    assert result.ok is True
    assert result.detail == "Connected."


def test_validate_betterstack_integration_forwards_failure_detail(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.integration_health.validate_betterstack_config",
        lambda _config: BetterStackValidationResult(
            ok=False, detail="Better Stack authentication failed."
        ),
    )
    result = validate_betterstack_integration(
        query_endpoint="https://x",
        username="u",
        password="wrong",
    )
    assert result.ok is False
    assert "authentication" in result.detail.lower()


def test_validate_betterstack_integration_accepts_empty_tables() -> None:
    # Tables are optional; calling with no tables must not crash and must not
    # call network (covered by the probe-level tests separately).
    result = validate_betterstack_integration(
        query_endpoint="",
        username="",
        password="",
    )
    # Empty config returns the "required" detail from the underlying probe.
    assert result.ok is False
    assert "required" in result.detail.lower()
