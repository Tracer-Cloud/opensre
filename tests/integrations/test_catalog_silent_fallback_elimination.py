"""Tests for #1468: eliminate silent env-loader / classify fallbacks.

Three patterns previously dropped exceptions to ``(None, None)``, ``pass``,
or ``logger.debug(..., exc_info=True)`` with no Sentry trace:

  A. ``_classify_service_instance`` per-vendor ``try/except Exception``
     blocks (38 sites covering grafana .. whatsapp; bitbucket/signoz/tempo/
     twilio/whatsapp were fixed in a later pass — see
     ``integrations._validation_helpers.report_classify_failure``, which now
     centrally wraps ``pydantic.ValidationError`` so no vendor classifier has
     to duplicate that guard itself).
  B. ``load_env_integrations`` argocd + helm ``except Exception: pass``.
  C. ``load_env_integrations`` debug-only env loaders (incident_io, mariadb,
     rabbitmq, rds, betterstack, alertmanager,
     victoria_logs, supabase).

After the fix every site routes through ``report_classify_failure`` (in
``integrations._validation_helpers``) or ``_report_env_loader_failure``
(in ``integrations._catalog_impl``), which call ``report_exception`` with
``surface=integration``, ``event=classify_failed`` / ``event=env_loader_failed``,
and the vendor tag — preserving the historic "skip the integration" caller
contract.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from integrations._catalog_impl import (
    _classify_service_instance,
    _report_env_loader_failure,
    load_env_integrations,
)
from integrations._validation_helpers import report_classify_failure


@pytest.fixture(autouse=True)
def _quiet_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_NO_TELEMETRY", "1")


# ---------------------------------------------------------------------------
# Helper smoke tests
# ---------------------------------------------------------------------------


def test_report_classify_failure_forwards_tags() -> None:
    exc = ValueError("bad config")
    with patch("integrations._validation_helpers.report_exception") as mock_report:
        report_classify_failure(
            exc,
            logger=__import__("logging").getLogger(__name__),
            integration="datadog",
            record_id="rec-1",
        )
    mock_report.assert_called_once()
    kwargs = mock_report.call_args.kwargs
    assert kwargs["severity"] == "warning"
    assert kwargs["tags"] == {
        "surface": "integration",
        "component": "integrations",
        "integration": "datadog",
        "event": "classify_failed",
    }
    assert kwargs["extras"] == {"record_id": "rec-1"}


def test_report_env_loader_failure_forwards_tags() -> None:
    exc = RuntimeError("env missing")
    with patch("integrations._catalog_impl.report_exception") as mock_report:
        _report_env_loader_failure(exc, integration="rabbitmq")
    mock_report.assert_called_once()
    kwargs = mock_report.call_args.kwargs
    assert kwargs["severity"] == "warning"
    assert kwargs["tags"] == {
        "surface": "integration",
        "component": "integrations._catalog_impl",
        "integration": "rabbitmq",
        "event": "env_loader_failed",
    }


# ---------------------------------------------------------------------------
# Pattern A: _classify_service_instance routes failures to Sentry
# ---------------------------------------------------------------------------


# Per-vendor: the module path and symbol whose constructor we force to raise,
# so we can prove the surrounding try/except now routes the failure through
# ``report_exception`` instead of returning ``(None, None)`` silently.
_CLASSIFY_PATCH_TARGETS: list[tuple[str, str, str]] = [
    ("grafana", "integrations.grafana", "GrafanaIntegrationConfig"),
    ("aws", "integrations.aws", "AWSIntegrationConfig"),
    ("datadog", "integrations.datadog", "DatadogIntegrationConfig"),
    ("honeycomb", "integrations.honeycomb", "HoneycombIntegrationConfig"),
    ("coralogix", "integrations.coralogix", "CoralogixIntegrationConfig"),
    ("new_relic", "integrations.new_relic", "NewRelicIntegrationConfig"),
    ("github", "integrations.github.mcp", "build_github_mcp_config"),
    ("sentry", "integrations.sentry", "build_sentry_config"),
    ("gitlab", "integrations.gitlab.client", "build_gitlab_config"),
    ("mongodb", "integrations.mongodb", "build_mongodb_config"),
    ("postgresql", "integrations.postgresql", "build_postgresql_config"),
    ("mongodb_atlas", "integrations.mongodb_atlas", "build_mongodb_atlas_config"),
    ("mariadb", "integrations.mariadb", "build_mariadb_config"),
    ("vercel", "integrations.vercel", "VercelConfig"),
    ("opsgenie", "integrations.opsgenie", "OpsGenieIntegrationConfig"),
    ("incident_io", "integrations.incident_io", "IncidentIoIntegrationConfig"),
    ("jira", "integrations.jira", "JiraIntegrationConfig"),
    ("discord", "integrations.discord", "DiscordBotConfig"),
    ("telegram", "integrations.telegram", "TelegramBotConfig"),
    ("mysql", "integrations.mysql", "build_mysql_config"),
    ("rabbitmq", "integrations.rabbitmq", "build_rabbitmq_config"),
    ("rds", "integrations.rds", "build_rds_config"),
    ("airflow", "integrations.airflow.config", "build_airflow_config"),
    ("betterstack", "integrations.betterstack", "build_betterstack_config"),
    ("azure_sql", "integrations.azure_sql", "build_azure_sql_config"),
    ("alertmanager", "integrations.alertmanager", "AlertmanagerIntegrationConfig"),
    ("argocd", "integrations.argocd", "ArgoCDIntegrationConfig"),
    ("helm", "integrations.helm", "HelmIntegrationConfig"),
    ("victoria_logs", "integrations.victoria_logs", "VictoriaLogsIntegrationConfig"),
    ("splunk", "integrations.splunk", "SplunkIntegrationConfig"),
    ("supabase", "integrations.supabase", "build_supabase_config"),
    ("smtp", "integrations.smtp", "SMTPIntegrationConfig"),
    # Previously silent or partially-reported (bitbucket/signoz/tempo dropped to
    # (None, None) with no report at all; twilio/whatsapp swallowed ValidationError
    # specifically). All five now route through report_classify_failure like every
    # other vendor above.
    ("bitbucket", "integrations.bitbucket.config", "BitbucketConfig"),
    ("signoz", "integrations.signoz.config", "build_signoz_config"),
    ("tempo", "integrations.tempo", "build_tempo_config"),
    ("twilio", "integrations.twilio", "TwilioIntegrationConfig"),
    ("whatsapp", "integrations.whatsapp", "WhatsAppConfig"),
]


@pytest.mark.parametrize(("integration", "patch_module", "patch_symbol"), _CLASSIFY_PATCH_TARGETS)
def test_classify_failure_skips_integration_and_reports(
    integration: str,
    patch_module: str,
    patch_symbol: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every Pattern A site (``_classify_service_instance``) must still return
    ``(None, None)`` *and* now route the exception through
    ``report_exception`` with ``event=classify_failed`` and the vendor tag."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"forced {integration} failure")

    monkeypatch.setattr(f"{patch_module}.{patch_symbol}", _boom)

    with patch("integrations._validation_helpers.report_exception") as mock_report:
        result = _classify_service_instance(
            integration,
            {"endpoint": "https://x", "api_key": "k", "bot_token": "fake-token"},
            record_id=f"rec-{integration}",
        )

    assert result == (None, None), (
        f"caller contract broken for {integration}: must still skip integration"
    )
    assert mock_report.call_count == 1, (
        f"{integration} silently swallowed exception (no report_exception call)"
    )
    tags = mock_report.call_args.kwargs["tags"]
    assert tags["integration"] == integration
    assert tags["event"] == "classify_failed"
    assert tags["surface"] == "integration"
    assert mock_report.call_args.kwargs["severity"] == "warning"
    assert mock_report.call_args.kwargs["extras"]["record_id"] == f"rec-{integration}"


@pytest.mark.parametrize("bot_token", ["", " ", None])
@pytest.mark.parametrize("integration", ["discord", "telegram"])
def test_classify_empty_bot_token_silently_skips(
    integration: str,
    bot_token: str | None,
) -> None:
    """Discord/Telegram records with empty bot_token are silently skipped without
    reporting to Sentry — empty token means 'not configured', not misconfigured."""
    credentials: dict[str, Any] = {"endpoint": "https://x"}
    if bot_token is not None:
        credentials["bot_token"] = bot_token

    with patch("integrations._validation_helpers.report_exception") as mock_report:
        result = _classify_service_instance(
            integration,
            credentials,
            record_id="rec-test",
        )

    assert result == (None, None)
    assert mock_report.call_count == 0, (
        f"{integration} with empty bot_token should not report to Sentry"
    )


# ---------------------------------------------------------------------------
# Pattern B + C: load_env_integrations routes loader failures to Sentry
# ---------------------------------------------------------------------------


# For each env-loader case: env vars to enable the loader path + the symbol in
# ``integrations._catalog_impl`` to patch so its constructor raises. This
# proves the exception is *caught* and *routed* — without depending on which
# fields the real builder rejects (validator details drift independently).
_ENV_LOADER_CASES: list[tuple[str, dict[str, str], str]] = [
    # Pattern B
    (
        "argocd",
        {"ARGOCD_BASE_URL": "https://argo.example", "ARGOCD_AUTH_TOKEN": "t"},
        "ArgoCDIntegrationConfig",
    ),
    (
        "helm",
        {"OSRE_HELM_INTEGRATION": "1"},
        "HelmIntegrationConfig",
    ),
    # Pattern C
    (
        "incident_io",
        {"INCIDENT_IO_API_KEY": "k"},
        "IncidentIoIntegrationConfig",
    ),
    (
        "mariadb",
        {"MARIADB_HOST": "h", "MARIADB_DATABASE": "d"},
        "build_mariadb_config",
    ),
    (
        "rabbitmq",
        {"RABBITMQ_HOST": "h", "RABBITMQ_USERNAME": "u"},
        "build_rabbitmq_config",
    ),
    (
        "betterstack",
        {"BETTERSTACK_QUERY_ENDPOINT": "https://bs.example", "BETTERSTACK_USERNAME": "u"},
        "build_betterstack_config",
    ),
    (
        "alertmanager",
        {"ALERTMANAGER_URL": "https://am.example"},
        "AlertmanagerIntegrationConfig",
    ),
    (
        "victoria_logs",
        {"VICTORIA_LOGS_URL": "https://vl.example"},
        "VictoriaLogsIntegrationConfig",
    ),
    (
        "supabase",
        {"SUPABASE_URL": "https://s.example", "SUPABASE_SERVICE_KEY": "k"},
        "build_supabase_config",
    ),
    (
        "rds",
        {},
        "rds_config_from_env",
    ),
    # Pattern C — added in this PR (was the #2036 follow-up scope).
    (
        "vercel",
        {"VERCEL_API_TOKEN": "t"},
        "VercelConfig",
    ),
    (
        "opsgenie",
        {"OPSGENIE_API_KEY": "k"},
        "OpsGenieIntegrationConfig",
    ),
    (
        "jira",
        {
            "JIRA_BASE_URL": "https://jira.example",
            "JIRA_EMAIL": "e@example.com",
            "JIRA_API_TOKEN": "t",
        },
        "JiraIntegrationConfig",
    ),
    (
        "discord",
        {"DISCORD_BOT_TOKEN": "t"},
        "DiscordBotConfig",
    ),
    (
        "telegram",
        {"TELEGRAM_BOT_TOKEN": "t"},
        "TelegramBotConfig",
    ),
    (
        "mongodb_atlas",
        {
            "MONGODB_ATLAS_PUBLIC_KEY": "pub",
            "MONGODB_ATLAS_PRIVATE_KEY": "priv",
            "MONGODB_ATLAS_PROJECT_ID": "proj",
        },
        "build_mongodb_atlas_config",
    ),
    # Early env-loader blocks that #1468 never guarded — a malformed cred here
    # aborted discovery of every later integration (the #2036 class of bug).
    (
        "grafana",
        {"GRAFANA_INSTANCE_URL": "https://g.example", "GRAFANA_READ_TOKEN": "t"},
        "GrafanaIntegrationConfig",
    ),
    (
        "datadog",
        {"DD_API_KEY": "k", "DD_APP_KEY": "a"},
        "DatadogIntegrationConfig",
    ),
    (
        "honeycomb",
        {"HONEYCOMB_API_KEY": "k"},
        "HoneycombIntegrationConfig",
    ),
    (
        "coralogix",
        {"CORALOGIX_API_KEY": "k"},
        "CoralogixIntegrationConfig",
    ),
    (
        "new_relic",
        {
            "NEW_RELIC_API_KEY": "NRAK-test-fake-0000000000000000000",
            "NEW_RELIC_ACCOUNT_ID": "9876543",
            "NEW_RELIC_API_URL": "https://api.eu.newrelic.com",
        },
        "NewRelicIntegrationConfig",
    ),
    (
        "aws",
        {"AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/x"},
        "AWSIntegrationConfig",
    ),
    (
        # The access-key path is a separate elif branch with its own guard.
        "aws",
        {"AWS_ACCESS_KEY_ID": "key", "AWS_SECRET_ACCESS_KEY": "secret"},
        "AWSIntegrationConfig",
    ),
    (
        "whatsapp",
        {
            "TWILIO_ACCOUNT_SID": "sid",
            "TWILIO_AUTH_TOKEN": "tok",
            "TWILIO_WHATSAPP_FROM": "+15551234567",
        },
        "WhatsAppConfig",
    ),
    (
        "splunk",
        {"SPLUNK_URL": "https://s.example", "SPLUNK_TOKEN": "t"},
        "SplunkIntegrationConfig",
    ),
    # Stragglers still on the pre-#1468 swallow pattern: signoz/jenkins/tempo
    # logged only at debug (exc_info=True); twilio dropped to None silently.
    (
        "signoz",
        {},
        "signoz_config_from_env",
    ),
    (
        "jenkins",
        {},
        "jenkins_config_from_env",
    ),
    (
        "tempo",
        {},
        "tempo_config_from_env",
    ),
    (
        "twilio",
        {
            "TWILIO_ACCOUNT_SID": "sid",
            "TWILIO_AUTH_TOKEN": "tok",
            "TWILIO_SMS_FROM": "+15551234567",
        },
        "TwilioIntegrationConfig",
    ),
]


@pytest.mark.parametrize(("integration", "env", "patch_symbol"), _ENV_LOADER_CASES)
def test_env_loader_failure_reports_and_skips(
    integration: str,
    env: dict[str, str],
    patch_symbol: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pattern B + C: when a vendor's env-derived builder raises, the
    integration must be skipped (preserving the historic caller contract)
    *and* the failure must reach Sentry via ``report_exception``."""
    # Clear ambient env so other integrations on the dev box don't enable
    # unrelated paths and inflate the assertion. Disable keyring too —
    # secret fields resolve via resolve_env_credential (env then keyring),
    # and a wiped env without this flag can hammer a broken session keyring
    # under xdist or leak secrets from a prior MemoryKeyring fixture.
    for var in list(os.environ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    for var, value in env.items():
        monkeypatch.setenv(var, value)

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"forced {integration} failure")

    monkeypatch.setattr(f"integrations._catalog_impl.{patch_symbol}", _boom)

    with patch("integrations._catalog_impl.report_exception") as mock_report:
        result = load_env_integrations()

    services = {entry["service"] for entry in result}
    assert integration not in services, f"{integration} must be skipped when its env loader fails"

    matching = [
        call
        for call in mock_report.call_args_list
        if call.kwargs.get("tags", {}).get("integration") == integration
    ]
    assert matching, (
        f"{integration} env-loader failure was swallowed silently — expected "
        "report_exception call with event=env_loader_failed"
    )
    tags = matching[0].kwargs["tags"]
    assert tags["event"] == "env_loader_failed"
    assert tags["surface"] == "integration"
    assert matching[0].kwargs["severity"] == "warning"


def test_one_failing_env_loader_does_not_abort_remaining_integrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression #2036 was filed for: a single failing ``model_validate``
    in ``load_env_integrations`` must not abort discovery for every later
    vendor. The survivor must run *after* the failing loader — otherwise the
    assertion passes even when the loop aborts on vercel (caught by
    @VibhorGautam in review).

    incident_io is loaded after vercel in ``load_env_integrations``, so its
    presence in the result proves the loop continued past the vercel failure.
    """
    for var in list(os.environ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    monkeypatch.setenv("VERCEL_API_TOKEN", "tkn")
    monkeypatch.setenv("INCIDENT_IO_API_KEY", "tkn")

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("forced vercel failure")

    monkeypatch.setattr("integrations._catalog_impl.VercelConfig", _boom)

    with patch("integrations._catalog_impl.report_exception"):
        result = load_env_integrations()

    services = {entry["service"] for entry in result}
    assert "vercel" not in services
    assert "incident_io" in services, (
        "incident_io was dropped — discovery aborted on the vercel failure instead of "
        "continuing past it (this is exactly the bug #2036 is about)"
    )
