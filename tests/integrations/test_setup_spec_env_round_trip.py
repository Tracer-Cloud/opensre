"""Every ``SetupField.env_var`` must be a name the catalog actually reads back.

:func:`integrations.setup_flow.apply_setup` writes credentials to ``.env`` and
the keyring; :func:`integrations._catalog_impl.load_env_integrations` is what
reads them again — and the two sides name the same value differently
(``base_url`` is written as ``HONEYCOMB_API_URL``, ``endpoint`` as
``GRAFANA_INSTANCE_URL``). A spec that declares an env var nothing reads still
passes every test in :mod:`tests.integrations.test_setup_flow`, because those
mock the writers: the value lands in ``.env`` and is silently never resolved
again. The failure surfaces only as a deploy preflight calling a fully
configured integration missing.

So this closes the loop end to end — persist through the real ``.env`` writer,
load the result into the environment, and require the catalog to hand back the
same credentials.
"""

from __future__ import annotations

import dataclasses
import functools
import os
from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
from config.env_file import env_assignment_key, read_env_lines, sync_env_values
from integrations._catalog_impl import load_env_integrations, resolve_effective_integrations
from integrations.alertmanager.setup import ALERTMANAGER_SETUP
from integrations.aws.setup import AWS_SETUP
from integrations.azure.setup import AZURE_SETUP
from integrations.azure_sql.setup import AZURE_SQL_SETUP
from integrations.betterstack.setup import BETTERSTACK_SETUP
from integrations.coralogix.setup import CORALOGIX_SETUP
from integrations.dagster.setup import DAGSTER_SETUP
from integrations.datadog.setup import DATADOG_SETUP
from integrations.gitlab.setup import GITLAB_SETUP
from integrations.google_docs import GOOGLE_DOCS_SETUP
from integrations.grafana.setup import GRAFANA_SETUP
from integrations.groundcover.setup import GROUNDCOVER_SETUP
from integrations.helm.setup import HELM_SETUP
from integrations.honeycomb.setup import HONEYCOMB_SETUP
from integrations.incident_io.setup import INCIDENT_IO_SETUP
from integrations.jenkins.setup import JENKINS_SETUP
from integrations.kubernetes.setup import KUBERNETES_SETUP
from integrations.mariadb.setup import MARIADB_SETUP
from integrations.mongodb.setup import MONGODB_SETUP
from integrations.mongodb_atlas.setup import MONGODB_ATLAS_SETUP
from integrations.mysql.setup import MYSQL_SETUP
from integrations.new_relic.setup import NEW_RELIC_SETUP
from integrations.opensearch.setup import OPENSEARCH_SETUP
from integrations.pagerduty.setup import PAGERDUTY_SETUP
from integrations.postgresql.setup import POSTGRESQL_SETUP
from integrations.posthog.setup import POSTHOG_SETUP
from integrations.posthog_mcp.setup import POSTHOG_MCP_SETUP
from integrations.rds.setup import RDS_SETUP
from integrations.redis.setup import REDIS_SETUP
from integrations.sentry.setup import SENTRY_SETUP
from integrations.sentry_mcp.setup import SENTRY_MCP_SETUP
from integrations.servicenow.setup import SERVICENOW_SETUP
from integrations.signoz.setup import SIGNOZ_SETUP
from integrations.slack.setup import SLACK_SETUP
from integrations.smtp.setup import SMTP_SETUP
from integrations.telegram.setup import TELEGRAM_SETUP
from integrations.tempo.setup import TEMPO_SETUP
from integrations.temporal.setup import TEMPORAL_SETUP
from integrations.tracer.setup import TRACER_SETUP
from integrations.twilio.setup import TWILIO_SETUP
from integrations.vercel.setup import VERCEL_SETUP
from integrations.whatsapp.setup import WHATSAPP_SETUP
from integrations.x_mcp.setup import X_MCP_SETUP

# A distinct, recognizable value per field, so two fields of the same
# integration swapping places fails instead of coincidentally matching. Values
# are deliberately non-default (EU hosts, a named dataset) — a default would
# still "round-trip" through a spec that wrote nothing at all.
_SUBMITTED: dict[str, dict[str, str]] = {
    "datadog": {"api_key": "dd-api-key", "app_key": "dd-app-key", "site": "datadoghq.eu"},
    "honeycomb": {
        "api_key": "hc-api-key",
        "dataset": "checkout-prod",
        "base_url": "https://api.eu1.honeycomb.io",
    },
    "coralogix": {
        "api_key": "cx-api-key",
        "base_url": "https://api.eu2.coralogix.com",
        "application_name": "checkout",
        "subsystem_name": "api",
    },
    "groundcover": {
        "api_key": "gc-api-key",
        "mcp_url": "https://mcp.eu.groundcover.com/api/mcp",
        "tenant_uuid": "11111111-2222-3333-4444-555555555555",
        "backend_id": "gc-backend-7",
        "timezone": "Europe/Berlin",
    },
    "gitlab": {
        "base_url": "https://gitlab.example.com/api/v4",
        "auth_token": "glpat-gitlab-token",
    },
    "google_docs": {
        "credentials_file": "/opt/opensre/google-docs-sa.json",
        "folder_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
    },
    "sentry": {
        "base_url": "https://sentry.example.com",
        "organization_slug": "checkout-org",
        "auth_token": "sntrys-sentry-token",
        "project_slug": "checkout-api",
    },
    "posthog": {
        "base_url": "https://eu.posthog.com",
        "project_id": "40182",
        "personal_api_key": "phx-posthog-key",
    },
    "vercel": {"api_token": "vercel-api-token", "team_id": "team_abc123"},
    "telegram": {"bot_token": "123456:tg-bot-token", "default_chat_id": "-1001234567890"},
    "incident_io": {"api_key": "iio-api-key", "base_url": "https://api.eu.incident.io"},
    "new_relic": {
        "api_key": "NRAK-test-fake-0000000000000000000",
        "account_id": "9876543",
        "base_url": "https://api.eu.newrelic.com",
    },
    "tracer": {"base_url": "https://tracer.example.com", "jwt_token": "tracer-jwt-token"},
    "mongodb_atlas": {
        "api_public_key": "atlas-public-key",
        "api_private_key": "atlas-private-key",
        "project_id": "60f1a2b3c4d5e6f7a8b9c0d1",
        "base_url": "https://cloud-eu.mongodb.com/api/atlas/v2",
    },
    "signoz": {"url": "https://signoz.example.com", "api_key": "signoz-api-key"},
    "jenkins": {
        "base_url": "https://jenkins.example.com",
        "username": "ci-bot",
        "api_token": "jenkins-api-token",
    },
    "pagerduty": {"api_key": "pd-api-key", "base_url": "https://api.eu.pagerduty.com"},
    "dagster": {"endpoint": "https://checkout.dagster.cloud/prod", "api_token": "dagster-token"},
    "temporal": {
        "base_url": "https://temporal.example.com",
        "namespace": "checkout-prod",
        "api_key": "temporal-api-key",
    },
    "helm": {
        "helm_path": "/opt/homebrew/bin/helm",
        "kube_context": "checkout-prod",
        "kubeconfig": "/home/ci/.kube/config",
        "default_namespace": "checkout",
    },
    "smtp": {
        "host": "smtp.eu.example.com",
        "port": "2525",
        "security": "ssl",
        "username": "reports@example.com",
        "password": "smtp-secret",
        "from_address": "reports@example.com",
        "default_to": "oncall@example.com",
    },
    "whatsapp": {
        "account_sid": "AC-checkout-sid",
        "auth_token": "twilio-auth-token",
        "from_number": "whatsapp:+14155238886",
        "default_to": "+15551234567",
    },
    "tempo": {
        "url": "https://tempo.eu.example.com",
        "api_key": "tempo-bearer-token",
        "username": "tempo-user",
        "password": "tempo-password",
        "org_id": "checkout-tenant",
    },
    "posthog_mcp": {
        "url": "https://mcp.eu.posthog.com/mcp",
        "auth_token": "phx_mcp_personal_api_key",
        "project_id": "checkout-project",
    },
    "sentry_mcp": {
        "url": "https://mcp.eu.sentry.dev/mcp",
        "auth_token": "sentry-user-auth-token",
        "host": "sentry.checkout.internal",
    },
    "x_mcp": {
        "url": "https://x-mcp.checkout.internal/mcp",
        "auth_token": "x-mcp-tunnel-token",
    },
    "betterstack": {
        "query_endpoint": "https://eu-nbg-2-connect.betterstackdata.com",
        "username": "bs-user",
        "password": "bs-password",
        "sources": "t1_checkout,t2_api",
    },
    "servicenow": {
        "instance_url": "https://dev12345.service-now.com",
        "username": "opensre",
        "password": "sn-password",
    },
    "postgresql": {
        "host": "postgres.eu.example.com",
        "database": "checkout",
        "port": "5432",
        "username": "opensre",
        "password": "pg-password",
        "ssl_mode": "require",
    },
    "mysql": {
        "host": "mysql.eu.example.com",
        "database": "checkout",
        "port": "3306",
        "username": "opensre",
        "password": "mysql-password",
        "ssl_mode": "required",
    },
    "mariadb": {
        "host": "mariadb.eu.example.com",
        "database": "checkout",
        "port": "3307",
        "username": "opensre",
        "password": "mariadb-password",
        "ssl": "false",
    },
    "mongodb": {
        "connection_string": "mongodb+srv://opensre:secret@cluster.eu.example.net",
        "database": "checkout",
        "auth_source": "opensre",
        "tls": "true",
    },
    "redis": {
        "host": "redis.eu.example.com",
        "port": "6380",
        "username": "opensre",
        "password": "redis-password",
        "db": "2",
        "ssl": "true",
    },
    "azure_sql": {
        "server": "checkout.database.windows.net",
        "database": "checkout",
        "port": "1433",
        "username": "opensre",
        "password": "azure-sql-password",
        "driver": "ODBC Driver 18 for SQL Server",
        "encrypt": "true",
    },
    "azure": {
        "workspace_id": "11111111-2222-3333-4444-555555555555",
        "access_token": "azure-log-analytics-token",
        "endpoint": "https://api.loganalytics.azure.us",
        "tenant_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "subscription_id": "ffffffff-0000-1111-2222-333333333333",
        "max_results": "150",
    },
    "grafana": {
        "endpoint": "https://checkout.grafana.net",
        "api_key": "glsa_grafana_token",
        "verify_ssl": "true",
        "ca_bundle": "/etc/ssl/certs/checkout-ca.pem",
    },
    "alertmanager": {
        # Catalog rejects bearer + basic together; exercise bearer alone.
        "base_url": "https://alertmanager.checkout.internal",
        "bearer_token": "am-bearer",
        "username": "",
        "password": "",
    },
    "opensearch": {
        "url": "https://opensearch.checkout.internal:9200",
        "api_key": "os-api-key",
        "username": "",
        "password": "",
    },
    "rds": {
        "db_instance_identifier": "checkout-prod",
        "region": "eu-west-1",
    },
    "slack": {
        # Webhook is store-only (no env_var); Socket Mode tokens round-trip via env.
        "webhook_url": "",
        "bot_token": "xoxb-test-token",
        "app_token": "xapp-test-token",
        "default_chat_id": "C0123ABCD",
    },
    "aws": {
        # Keys mode — role fields cleared. Catalog hydrates flat access-key credentials.
        "region": "eu-west-1",
        "role_arn": "",
        "external_id": "",
        "access_key_id": "AKIAEXAMPLEKEYID01",
        "secret_access_key": "aws-secret-access-key",
        "session_token": "aws-session-token",
    },
    "github": {
        "mode": "streamable-http",
        "url": "https://api.githubcopilot.com/mcp/",
        "auth_token": "gho_github_token",
        "toolsets": "repos,issues,pull_requests",
        # Store-only; env bootstrap never writes a username.
        "username": "",
    },
    "kubernetes": {
        "kubeconfig_path": "/tmp/opensre-test-kubeconfig",
        "kubeconfig": "",
        "context": "checkout-prod",
        "namespace": "opensre",
    },
}

# Helm's env-only catalog discovery additionally gates on ``OSRE_HELM_INTEGRATION``
# (see integrations/helm/setup.py) — a manual opt-in unrelated to any SetupField,
# so it is set alongside the persisted values rather than through apply_setup.
_EXTRA_ENV: dict[str, dict[str, str]] = {
    "helm": {"OSRE_HELM_INTEGRATION": "true"},
}

_SPECS = [
    CORALOGIX_SETUP,
    DAGSTER_SETUP,
    DATADOG_SETUP,
    GITLAB_SETUP,
    GOOGLE_DOCS_SETUP,
    GROUNDCOVER_SETUP,
    HELM_SETUP,
    HONEYCOMB_SETUP,
    INCIDENT_IO_SETUP,
    JENKINS_SETUP,
    MONGODB_ATLAS_SETUP,
    NEW_RELIC_SETUP,
    PAGERDUTY_SETUP,
    POSTHOG_SETUP,
    SENTRY_SETUP,
    SIGNOZ_SETUP,
    SMTP_SETUP,
    TELEGRAM_SETUP,
    TEMPO_SETUP,
    TEMPORAL_SETUP,
    TRACER_SETUP,
    VERCEL_SETUP,
    WHATSAPP_SETUP,
    POSTHOG_MCP_SETUP,
    SENTRY_MCP_SETUP,
    X_MCP_SETUP,
    BETTERSTACK_SETUP,
    SERVICENOW_SETUP,
    POSTGRESQL_SETUP,
    MYSQL_SETUP,
    MARIADB_SETUP,
    MONGODB_SETUP,
    REDIS_SETUP,
    AZURE_SQL_SETUP,
    AZURE_SETUP,
    GRAFANA_SETUP,
    ALERTMANAGER_SETUP,
    OPENSEARCH_SETUP,
    RDS_SETUP,
    SLACK_SETUP,
    AWS_SETUP,
    KUBERNETES_SETUP,
]


@dataclasses.dataclass
class _Persisted:
    """Where a run's credentials ended up."""

    env_path: Path
    secrets: dict[str, str]


@pytest.fixture
def persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Persisted:
    """Point the flow's writers at a throwaway ``.env`` and an in-memory keyring.

    The real :func:`config.env_file.sync_env_values` is kept and only its target
    moves, so its refusal to write a sensitive key to disk still applies. Only
    the keyring backend is replaced — a test must not touch the real one.
    """
    written = _Persisted(env_path=tmp_path / ".env", secrets={})
    monkeypatch.setattr(
        setup_flow,
        "sync_env_values",
        functools.partial(sync_env_values, env_path=written.env_path),
    )
    monkeypatch.setattr(setup_flow, "sync_env_secret", written.secrets.__setitem__)
    monkeypatch.setattr(setup_flow, "upsert_integration", lambda _service, _payload: None)
    return written


# Left in place by the wipe below: process/runtime plumbing that unrelated
# code (subprocess calls, a keyring backend resolving a config path under
# $HOME) may need, and that no vendor's env_var is ever named after. Keeping
# this list short and explicit — rather than only clearing known vendor names
# — is what lets the wipe still catch a *wrong* env_var: the credential names
# are exactly what must be guaranteed absent unless apply_setup wrote them.
_ENV_PLUMBING = frozenset({"HOME", "PATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "USER"})


def _restore_environment(written: _Persisted, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduce the environment a later process would start with.

    Every existing env var is cleared first (aside from ``_ENV_PLUMBING``).
    ``tests/conftest.py`` loads the developer's real local ``.env`` into
    ``os.environ`` for the whole test session, so without this a spec whose
    ``env_var`` is wrong can still "round-trip" — not from what this test just
    persisted, but from a same-named value already sitting in that real
    ``.env`` (Twilio's shared account vars are a real example: a wrong
    ``env_var`` still resolved because the correct name happened to already be
    set for real).

    Keyring secrets are seeded straight into ``os.environ`` because
    ``resolve_env_credential`` checks the environment first — which is what a
    deploy, and this assertion, ultimately depend on.
    """
    for key in list(os.environ):
        if key not in _ENV_PLUMBING:
            monkeypatch.delenv(key, raising=False)
    # The wipe above also removes the root conftest's OPENSRE_DISABLE_KEYRING;
    # put it back so an unset field falling through to resolve_keyring_secret
    # still misses cleanly instead of touching a real OS keyring.
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    for key, value in written.secrets.items():
        monkeypatch.setenv(key, value)
    for line in read_env_lines(written.env_path):
        key = env_assignment_key(line)
        if key:
            monkeypatch.setenv(key, line.split("=", 1)[1].strip())


def _matches_catalog_value(got: Any, expected: str) -> bool:
    """Compare across env-string ↔ catalog-model normalizations.

    Ports become ints; bools become True/False; comma/space-separated hints
    become sequences. Neither change is a persistence bug — the names and
    values still round-trip. An absent optional credential (``None``) matches
    a blank submitted value — classifiers often omit empty keys.
    """
    if got is None:
        return expected == ""
    if isinstance(got, bool):
        return got is (expected.strip().lower() in ("true", "1", "yes"))
    if isinstance(got, (list, tuple)):
        if "," in expected:
            return list(got) == [part.strip() for part in expected.split(",") if part.strip()]
        return list(got) == [part for part in expected.split() if part]
    return str(got) == expected


def _catalog_credentials(service: str) -> dict[str, Any]:
    # Tracer is the one integration whose env-only discovery lives outside
    # ``load_env_integrations`` entirely — it is a top-level fallback inside
    # ``resolve_effective_integrations`` (see ``_catalog_impl.py``), not a
    # per-vendor block in the env loader. ``store_integrations=[]`` keeps this
    # off the real local store.
    if service == "tracer":
        entry = resolve_effective_integrations(store_integrations=[]).get("tracer")
        if not isinstance(entry, dict):
            raise AssertionError(f"{service} was not discovered from the environment")
        config = entry.get("config")
        assert isinstance(config, dict)
        return config
    for record in load_env_integrations():
        if record.get("service") == service:
            credentials = record.get("credentials")
            assert isinstance(credentials, dict)
            return credentials
    raise AssertionError(f"{service} was not discovered from the environment")


@pytest.mark.parametrize("spec", _SPECS, ids=lambda spec: spec.service)
def test_persisted_credentials_are_read_back_by_the_catalog(
    spec: setup_flow.IntegrationSetupSpec, persisted: _Persisted, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = _SUBMITTED[spec.service]
    assert {field.name for field in spec.fields} == set(submitted), (
        f"{spec.service} spec fields changed; update this test's submitted values"
    )

    # Verification and reference resolution are each integration's own concern
    # and covered per vendor. Dropping them here keeps the test on one question:
    # do the values come back out under the names the spec wrote them?
    outcome = setup_flow.apply_setup(
        dataclasses.replace(spec, verify=None, resolve=None), submitted
    )
    assert outcome.ok, outcome.detail

    _restore_environment(persisted, monkeypatch)
    for key, value in _EXTRA_ENV.get(spec.service, {}).items():
        monkeypatch.setenv(key, value)

    resolved = _catalog_credentials(spec.service)
    for field in spec.fields:
        assert _matches_catalog_value(resolved.get(field.name), submitted[field.name]), (
            f"{spec.service}.{field.name} was persisted as {field.env_var!r}, "
            "which the catalog does not read back into that credential"
        )


def test_twilio_persisted_credentials_are_read_back_by_the_catalog(
    persisted: _Persisted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twilio stores SMS fields flat; the catalog rehydrates a nested ``sms`` dict."""
    submitted = {
        "account_sid": "ACtwilioaccountsid0001",
        "auth_token": "twilio-auth-token",
        "from_number": "+14155551234",
        "messaging_service_sid": "",
        "default_to": "+14155559876",
    }
    assert {field.name for field in TWILIO_SETUP.fields} == set(submitted)

    outcome = setup_flow.apply_setup(
        dataclasses.replace(TWILIO_SETUP, verify=None, resolve=None), submitted
    )
    assert outcome.ok, outcome.detail

    _restore_environment(persisted, monkeypatch)
    resolved = _catalog_credentials("twilio")
    assert resolved.get("account_sid") == submitted["account_sid"]
    assert resolved.get("auth_token") == submitted["auth_token"]
    sms = resolved.get("sms") or {}
    assert isinstance(sms, dict)
    assert sms.get("from_number") == submitted["from_number"]
    assert sms.get("messaging_service_sid") == submitted["messaging_service_sid"]
    assert sms.get("default_to") == submitted["default_to"]
