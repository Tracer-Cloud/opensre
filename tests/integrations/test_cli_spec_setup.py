"""Behavior of the spec-driven ``opensre integrations setup <service>`` handlers.

One parametrized suite rather than a file per vendor: the handlers are now a
two-line delegation to :func:`integrations.cli._run_spec_setup`, so what is
worth pinning is the same for each — the prompt order and which answers are
masked, that nothing is written until verification passes, and that the
credentials reach the keyring and ``.env`` rather than the store alone.

That last one is the migration's point. These handlers previously called
``upsert_integration`` and stopped, which reads fine at runtime (the store is
resolved first) but leaves the deploy preflight — which reads env vars —
declaring a working integration missing.

Vendor-specific behavior stays with the vendor:
:mod:`tests.integrations.telegram.test_cli_setup_characterization` covers
Telegram's chat-id resolution, and
:mod:`tests.integrations.test_setup_spec_env_round_trip` covers the env var
names themselves.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

import integrations.alertmanager.setup as alertmanager_setup
import integrations.azure.setup as azure_setup
import integrations.azure_sql.setup as azure_sql_setup
import integrations.betterstack.setup as betterstack_setup
import integrations.cli as cli
import integrations.coralogix.setup as coralogix_setup
import integrations.dagster.setup as dagster_setup
import integrations.datadog.setup as datadog_setup
import integrations.gitlab.setup as gitlab_setup
import integrations.google_docs as google_docs_setup
import integrations.grafana.setup as grafana_setup
import integrations.groundcover.setup as groundcover_setup
import integrations.helm.setup as helm_setup
import integrations.honeycomb.setup as honeycomb_setup
import integrations.incident_io.setup as incident_io_setup
import integrations.jenkins.setup as jenkins_setup
import integrations.mariadb.setup as mariadb_setup
import integrations.mongodb.setup as mongodb_setup
import integrations.mongodb_atlas.setup as mongodb_atlas_setup
import integrations.mysql.setup as mysql_setup
import integrations.new_relic.setup as new_relic_setup
import integrations.opensearch.setup as opensearch_setup
import integrations.pagerduty.setup as pagerduty_setup
import integrations.postgresql.setup as postgresql_setup
import integrations.posthog.setup as posthog_setup
import integrations.posthog_mcp.setup as posthog_mcp_setup
import integrations.rds.setup as rds_setup
import integrations.redis.setup as redis_setup
import integrations.sentry.setup as sentry_setup
import integrations.sentry_mcp.setup as sentry_mcp_setup
import integrations.servicenow.setup as servicenow_setup
import integrations.setup_flow as setup_flow
import integrations.signoz.setup as signoz_setup
import integrations.slack.setup as slack_setup
import integrations.smtp.setup as smtp_setup
import integrations.tempo.setup as tempo_setup
import integrations.temporal.setup as temporal_setup
import integrations.tracer.setup as tracer_setup
import integrations.vercel.setup as vercel_setup
import integrations.whatsapp.setup as whatsapp_setup
import integrations.x_mcp.setup as x_mcp_setup

# Answers for *prompted* fields only. Constant fields are injected by the flow.
_ANSWERS: dict[str, dict[str, str]] = {
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
        "from_address": "reports@example.com",
        "port": "2525",
        "security": "ssl",
        "username": "reports@example.com",
        "password": "smtp-secret",
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
        "port": "3306",
        "username": "opensre",
        "password": "mariadb-password",
        "ssl": "false",
    },
    "mongodb": {
        "connection_string": "mongodb+srv://opensre:secret@cluster.eu.example.net",
        "database": "checkout",
        "auth_source": "admin",
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
        # Bearer XOR basic — catalog rejects both together.
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
        "webhook_url": "https://hooks.slack.com/services/T/B/xxx",
        "bot_token": "xoxb-test-bot-token",
        "app_token": "xapp-test-app-token",
    },
}

# (spec module, spec attribute, CLI handler) — the attribute is patched rather
# than the spec object because ``_setup_*`` imports it inside the function body.
_CASES = [
    pytest.param(datadog_setup, "DATADOG_SETUP", cli._setup_datadog, id="datadog"),
    pytest.param(honeycomb_setup, "HONEYCOMB_SETUP", cli._setup_honeycomb, id="honeycomb"),
    pytest.param(coralogix_setup, "CORALOGIX_SETUP", cli._setup_coralogix, id="coralogix"),
    pytest.param(groundcover_setup, "GROUNDCOVER_SETUP", cli._setup_groundcover, id="groundcover"),
    pytest.param(gitlab_setup, "GITLAB_SETUP", cli._setup_gitlab, id="gitlab"),
    pytest.param(google_docs_setup, "GOOGLE_DOCS_SETUP", cli._setup_google_docs, id="google_docs"),
    pytest.param(sentry_setup, "SENTRY_SETUP", cli._setup_sentry, id="sentry"),
    pytest.param(posthog_setup, "POSTHOG_SETUP", cli._setup_posthog, id="posthog"),
    pytest.param(vercel_setup, "VERCEL_SETUP", cli._setup_vercel, id="vercel"),
    pytest.param(incident_io_setup, "INCIDENT_IO_SETUP", cli._setup_incident_io, id="incident_io"),
    pytest.param(tracer_setup, "TRACER_SETUP", cli._setup_tracer, id="tracer"),
    pytest.param(
        mongodb_atlas_setup, "MONGODB_ATLAS_SETUP", cli._setup_mongodb_atlas, id="mongodb_atlas"
    ),
    pytest.param(signoz_setup, "SIGNOZ_SETUP", cli._setup_signoz, id="signoz"),
    pytest.param(jenkins_setup, "JENKINS_SETUP", cli._setup_jenkins, id="jenkins"),
    pytest.param(pagerduty_setup, "PAGERDUTY_SETUP", cli._setup_pagerduty, id="pagerduty"),
    pytest.param(dagster_setup, "DAGSTER_SETUP", cli._setup_dagster, id="dagster"),
    pytest.param(temporal_setup, "TEMPORAL_SETUP", cli._setup_temporal, id="temporal"),
    pytest.param(helm_setup, "HELM_SETUP", cli._setup_helm, id="helm"),
    pytest.param(smtp_setup, "SMTP_SETUP", cli._setup_smtp, id="smtp"),
    pytest.param(whatsapp_setup, "WHATSAPP_SETUP", cli._setup_whatsapp, id="whatsapp"),
    pytest.param(tempo_setup, "TEMPO_SETUP", cli._setup_tempo, id="tempo"),
    pytest.param(posthog_mcp_setup, "POSTHOG_MCP_SETUP", cli._setup_posthog_mcp, id="posthog_mcp"),
    pytest.param(sentry_mcp_setup, "SENTRY_MCP_SETUP", cli._setup_sentry_mcp, id="sentry_mcp"),
    pytest.param(x_mcp_setup, "X_MCP_SETUP", cli._setup_x_mcp, id="x_mcp"),
    pytest.param(betterstack_setup, "BETTERSTACK_SETUP", cli._setup_betterstack, id="betterstack"),
    pytest.param(servicenow_setup, "SERVICENOW_SETUP", cli._setup_servicenow, id="servicenow"),
    pytest.param(postgresql_setup, "POSTGRESQL_SETUP", cli._setup_postgresql, id="postgresql"),
    pytest.param(mysql_setup, "MYSQL_SETUP", cli._setup_mysql, id="mysql"),
    pytest.param(mariadb_setup, "MARIADB_SETUP", cli._setup_mariadb, id="mariadb"),
    pytest.param(mongodb_setup, "MONGODB_SETUP", cli._setup_mongodb, id="mongodb"),
    pytest.param(redis_setup, "REDIS_SETUP", cli._setup_redis, id="redis"),
    pytest.param(azure_sql_setup, "AZURE_SQL_SETUP", cli._setup_azure_sql, id="azure_sql"),
    pytest.param(azure_setup, "AZURE_SETUP", cli._setup_azure, id="azure"),
    pytest.param(grafana_setup, "GRAFANA_SETUP", cli._setup_grafana, id="grafana"),
    pytest.param(rds_setup, "RDS_SETUP", cli._setup_rds, id="rds"),
    pytest.param(new_relic_setup, "NEW_RELIC_SETUP", cli._setup_new_relic, id="new_relic"),
    # alertmanager, opensearch, and slack drive a mode picker rather than flat
    # linear prompts, so they get dedicated tests below instead of _CASES.
]


@dataclasses.dataclass
class _Run:
    """Scripted verifier outcome for one run, plus everything the run did."""

    verify_status: str = "passed"
    verify_detail: str = "Connected."

    asked: list[tuple[str, str, bool]] = dataclasses.field(default_factory=list)
    verified: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    store: list[tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=list)
    keyring: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    env: list[dict[str, str]] = dataclasses.field(default_factory=list)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> _Run:
    state = _Run()
    # Prefill reads the store; keep it empty so prompt defaults come from the
    # spec, not whatever the developer's real store happens to hold.
    monkeypatch.setattr("integrations.store.get_integration", lambda _service: None)
    monkeypatch.setattr(
        setup_flow,
        "upsert_integration",
        lambda service, payload: state.store.append((service, payload)),
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: state.keyring.append((key, value))
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_values", lambda values, **_kw: state.env.append(dict(values))
    )
    return state


def _prompted(spec: setup_flow.IntegrationSetupSpec) -> list[setup_flow.SetupField]:
    return [field for field in spec.fields if not field.is_constant]


def _expected_credentials(
    spec: setup_flow.IntegrationSetupSpec, answers: dict[str, str]
) -> dict[str, str | None]:
    credentials: dict[str, str | None] = {}
    for field in spec.fields:
        if field.is_constant:
            credentials[field.name] = field.constant
        else:
            # apply_setup stores blank optional answers as None, not "".
            credentials[field.name] = answers[field.name] or None
    return credentials


def _install(
    monkeypatch: pytest.MonkeyPatch, module: Any, attr: str, state: _Run, blank: str = ""
) -> setup_flow.IntegrationSetupSpec:
    """Swap in a stub verifier and script ``_p`` with this vendor's answers.

    Pass *blank* to answer one field with an empty string instead.
    """

    def _fake_verify(_source: str, config: dict[str, Any]) -> dict[str, str]:
        state.verified.append(dict(config))
        return {"status": state.verify_status, "detail": state.verify_detail}

    spec = dataclasses.replace(getattr(module, attr), guide=None, verify=_fake_verify)
    monkeypatch.setattr(module, attr, spec)

    answers = _ANSWERS[spec.service]
    queue = ["" if field.name == blank else answers[field.name] for field in _prompted(spec)]

    def _fake_p(label: str, default: str = "", secret: bool = False) -> str:
        state.asked.append((label, default, secret))
        return queue.pop(0)

    monkeypatch.setattr(cli, "_p", _fake_p)
    return spec


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_prompts_follow_the_spec_and_mask_only_secret_fields(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    spec = _install(monkeypatch, module, attr, run)
    prompted = _prompted(spec)

    handler()

    assert [label for label, _default, _secret in run.asked] == [
        field.question for field in prompted
    ]
    assert [secret for _label, _default, secret in run.asked] == [
        field.secret for field in prompted
    ]


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_defaults_are_offered_as_prompt_prefills(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    """A user pressing enter should land on the documented default, not blank."""
    spec = _install(monkeypatch, module, attr, run)

    handler()

    assert [default for _label, default, _secret in run.asked] == [
        field.default for field in _prompted(spec)
    ]


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_credentials_reach_the_keyring_and_env_not_just_the_store(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    spec = _install(monkeypatch, module, attr, run)
    answers = _ANSWERS[spec.service]
    expected = _expected_credentials(spec, answers)

    handler()

    assert run.store == [(spec.service, {"credentials": expected})]
    secret_fields = {
        f.env_var: (f.constant if f.is_constant else answers[f.name])
        for f in spec.fields
        if f.secret and f.env_var
    }
    assert dict(run.keyring) == secret_fields
    plain_fields = {
        f.env_var: (f.constant if f.is_constant else answers[f.name])
        for f in spec.fields
        if f.env_var and not f.secret
    }
    assert run.env == [plain_fields]


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_failed_verification_exits_without_saving(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    """A bad credential must not overwrite a working integration."""
    run.verify_status = "failed"
    run.verify_detail = "Rejected."
    _install(monkeypatch, module, attr, run)

    with pytest.raises(SystemExit):
        handler()

    assert (run.store, run.keyring, run.env) == ([], [], [])


@pytest.mark.parametrize(("module", "attr", "handler"), _CASES)
def test_blank_required_field_is_asked_again_not_fatal(
    monkeypatch: pytest.MonkeyPatch, run: _Run, module: Any, attr: str, handler: Any
) -> None:
    """A blank required answer re-asks that field; it never exits the setup.

    An empty answer at an interactive prompt is a normal thing to type, not an
    error to bail on — bailing left the user with "X is required", a non-zero
    exit, and no way forward but starting over. Ctrl+C is the way out.
    """
    spec = getattr(module, attr)
    prompted = _prompted(spec)
    first_required = next((f for f in prompted if f.required and not f.default), None)
    if first_required is None:
        pytest.skip(f"{spec.service} has no required prompted field without a default")

    # Arrange — script the prompts: every field answered normally, except the
    # required one is answered blank first and with the real value on re-ask.
    def _fake_verify(_source: str, config: dict[str, Any]) -> dict[str, str]:
        run.verified.append(dict(config))
        return {"status": "passed", "detail": "ok"}

    monkeypatch.setattr(module, attr, dataclasses.replace(spec, guide=None, verify=_fake_verify))
    answers = _ANSWERS[spec.service]
    queue: list[str] = []
    for field in prompted:
        if field.name == first_required.name:
            queue.append("")
        queue.append(answers[field.name])

    def _fake_p(label: str, default: str = "", secret: bool = False) -> str:
        run.asked.append((label, default, secret))
        return queue.pop(0)

    monkeypatch.setattr(cli, "_p", _fake_p)

    # Act
    handler()

    # Assert — asked twice (blank, then real), then verified and saved
    labels = [label for label, _default, _secret in run.asked]
    assert labels.count(first_required.question) == 2
    assert run.verified and run.store


# --- Mode-picker integrations (Slack, Alertmanager, OpenSearch) ----------------
#
# These drive a picker rather than flat prompts: the chosen mode decides which
# fields are asked, and a field belonging to another mode is cleared (stored as
# None), not left over from a previous run. apply_setup itself never sees the
# mode — the picker is purely a collection concern — so the store, keyring, and
# env assertions below exercise the collector, not apply_setup.


def _drive_picker(
    monkeypatch: pytest.MonkeyPatch,
    run: _Run,
    module: Any,
    attr: str,
    handler: Any,
    *,
    mode: str,
    values: dict[str, str],
    stored: dict[str, Any] | None = None,
) -> None:
    """Choose *mode* in the picker and answer the fields it collects.

    A blank entry in *values* simulates pressing enter, so the field lands on
    its prompt default (the stored value, if any) — the way the real ``_p``
    behaves.
    """

    def _fake_verify(_source: str, config: dict[str, Any]) -> dict[str, str]:
        run.verified.append(dict(config))
        return {"status": run.verify_status, "detail": run.verify_detail}

    spec = dataclasses.replace(getattr(module, attr), guide=None, verify=_fake_verify)
    monkeypatch.setattr(module, attr, spec)
    if stored is not None:
        monkeypatch.setattr(
            "integrations.store.get_integration", lambda _s: {"credentials": stored}
        )

    monkeypatch.setattr(cli, "_select", lambda *_a, **_k: mode)

    prompted = [f for f in spec.collectable_fields(mode) if not f.is_constant]
    answers = iter(values.get(f.name, "") for f in prompted)

    def _fake_p(label: str, default: str = "", secret: bool = False) -> str:
        run.asked.append((label, default, secret))
        return next(answers, "") or default

    monkeypatch.setattr(cli, "_p", _fake_p)
    handler()


def test_slack_webhook_mode_clears_socket_tokens(
    monkeypatch: pytest.MonkeyPatch, run: _Run
) -> None:
    hook = "https://hooks.slack.com/services/T/B/x"
    _drive_picker(
        monkeypatch,
        run,
        slack_setup,
        "SLACK_SETUP",
        cli._setup_slack,
        mode="webhook",
        values={"webhook_url": hook},
    )
    assert run.store == [
        (
            "slack",
            {
                "credentials": {
                    "webhook_url": hook,
                    "bot_token": None,
                    "app_token": None,
                    "default_chat_id": None,
                }
            },
        )
    ]
    # Webhook is store-only (no env_var). The unchosen socket tokens clear their
    # keyring slots so a prior Socket Mode setup does not linger in the env.
    assert dict(run.keyring) == {"SLACK_BOT_TOKEN": "", "SLACK_APP_TOKEN": ""}


def test_slack_both_mode_stores_all_three(monkeypatch: pytest.MonkeyPatch, run: _Run) -> None:
    hook = "https://hooks.slack.com/services/T/B/x"
    _drive_picker(
        monkeypatch,
        run,
        slack_setup,
        "SLACK_SETUP",
        cli._setup_slack,
        mode="both",
        values={"webhook_url": hook, "bot_token": "xoxb-1", "app_token": "xapp-1"},
    )
    assert run.store == [
        (
            "slack",
            {
                "credentials": {
                    "webhook_url": hook,
                    "bot_token": "xoxb-1",
                    "app_token": "xapp-1",
                    "default_chat_id": None,
                }
            },
        )
    ]


def test_slack_both_mode_prefills_stored_tokens_on_rerun(
    monkeypatch: pytest.MonkeyPatch, run: _Run
) -> None:
    """Re-running with 'both' and pressing enter keeps the stored credentials."""
    stored = {
        "webhook_url": "https://hooks.slack.com/services/T/B/x",
        "bot_token": "xoxb-1",
        "app_token": "xapp-1",
        "default_chat_id": None,
    }
    _drive_picker(
        monkeypatch,
        run,
        slack_setup,
        "SLACK_SETUP",
        cli._setup_slack,
        mode="both",
        values={},
        stored=stored,
    )
    assert run.store == [("slack", {"credentials": stored})]


def test_alertmanager_none_mode_stores_url_only(monkeypatch: pytest.MonkeyPatch, run: _Run) -> None:
    _drive_picker(
        monkeypatch,
        run,
        alertmanager_setup,
        "ALERTMANAGER_SETUP",
        cli._setup_alertmanager,
        mode="none",
        values={"base_url": "https://am.internal"},
    )
    assert run.store == [
        (
            "alertmanager",
            {
                "credentials": {
                    "base_url": "https://am.internal",
                    "bearer_token": None,
                    "username": None,
                    "password": None,
                }
            },
        )
    ]


def test_alertmanager_basic_mode_clears_bearer(monkeypatch: pytest.MonkeyPatch, run: _Run) -> None:
    _drive_picker(
        monkeypatch,
        run,
        alertmanager_setup,
        "ALERTMANAGER_SETUP",
        cli._setup_alertmanager,
        mode="basic",
        values={"base_url": "https://am.internal", "username": "ops", "password": "pw"},
    )
    assert run.store == [
        (
            "alertmanager",
            {
                "credentials": {
                    "base_url": "https://am.internal",
                    "bearer_token": None,
                    "username": "ops",
                    "password": "pw",
                }
            },
        )
    ]


def test_opensearch_api_key_mode_clears_basic_auth(
    monkeypatch: pytest.MonkeyPatch, run: _Run
) -> None:
    _drive_picker(
        monkeypatch,
        run,
        opensearch_setup,
        "OPENSEARCH_SETUP",
        cli._setup_opensearch,
        mode="api_key",
        values={"url": "https://os.internal", "api_key": "k"},
    )
    assert run.store == [
        (
            "opensearch",
            {
                "credentials": {
                    "url": "https://os.internal",
                    "api_key": "k",
                    "username": None,
                    "password": None,
                }
            },
        )
    ]
