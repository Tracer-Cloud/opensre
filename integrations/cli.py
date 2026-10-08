"""Interactive CLI for managing local integrations (~/.opensre/integrations.json).

Usage:
    python -m integrations setup <service>
    python -m integrations list
    python -m integrations show <service>
    python -m integrations remove <service>
    python -m integrations verify [service] [--send-slack-test]
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import questionary

from infrastructure.terminal.theme import (
    ANSI_BOLD,
    ANSI_RESET,
    GLYPH_SUCCESS,
)

if TYPE_CHECKING:
    from integrations.setup_flow import IntegrationSetupSpec

from integrations.github import setup_github
from integrations.registry import SUPPORTED_SETUP_SERVICES, resolve_management_service
from integrations.setup import (
    confirm as _confirm,
)
from integrations.setup import (
    die as _die,
)
from integrations.setup import (
    prompt_value as _p,
)
from integrations.setup import (
    select as _select,
)
from integrations.store import (
    get_integration,
    list_integrations,
    remove_integration,
    resolve_store_path,
)
from integrations.verify import (
    SUPPORTED_VERIFY_SERVICES,
    format_verification_results,
    verification_exit_code,
    verify_integrations,
)
from integrations.webapp_vault import delete_webapp_org_integration

_B = ANSI_BOLD
_R = ANSI_RESET


def _json_echo(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


_SECRET_KEYS = frozenset(
    {
        "api_token",
        "api_key",
        "api_private_key",
        "app_key",
        "bearer_token",
        "bot_token",
        "password",
        "secret_access_key",
        "session_token",
        "jwt_token",
        "webhook_url",
        "auth_token",
        "connection_string",
    }
)


def _parse_port(raw: str, default: int = 3306) -> int:
    """Parse a port string, returning *default* for invalid or out-of-range values."""
    try:
        port = int(raw)
    except (ValueError, TypeError):
        return default
    if port < 1 or port > 65535:
        return default
    return port


def _mask(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: (v[:4] + "****" if isinstance(v, str) and v else "****")
            if k in _SECRET_KEYS
            else _mask(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask(i) for i in obj]
    return obj


# ─── setup flows ──────────────────────────────────────────────────────────────


def _setup_grafana() -> None:
    from integrations.grafana.setup import GRAFANA_SETUP

    _run_spec_setup(GRAFANA_SETUP)


def _setup_datadog() -> None:
    from integrations.datadog.setup import DATADOG_SETUP

    _run_spec_setup(DATADOG_SETUP)


def _setup_groundcover() -> None:
    from integrations.groundcover.setup import GROUNDCOVER_SETUP

    _run_spec_setup(GROUNDCOVER_SETUP)


def _setup_honeycomb() -> None:
    from integrations.honeycomb.setup import HONEYCOMB_SETUP

    _run_spec_setup(HONEYCOMB_SETUP)


def _setup_coralogix() -> None:
    from integrations.coralogix.setup import CORALOGIX_SETUP

    _run_spec_setup(CORALOGIX_SETUP)


def _setup_new_relic() -> None:
    from integrations.new_relic.setup import NEW_RELIC_SETUP

    _run_spec_setup(NEW_RELIC_SETUP)


def _setup_aws() -> None:
    from integrations.aws.role_mode_gate import (
        CONFIGURE_FIRST_INSTRUCTION,
        GATE_OPTIONS,
        GATE_QUESTION,
        NO_AMBIENT_CREDENTIALS_NOTICE,
        ConfigureCredentialsFirst,
        RoleGateChoice,
        gate_role_mode,
    )
    from integrations.aws.setup import AWS_SETUP

    def _ask_gate() -> RoleGateChoice | None:
        print(f"\n  {NO_AMBIENT_CREDENTIALS_NOTICE}\n")
        print("  Your options:")
        for number, option in enumerate(GATE_OPTIONS, start=1):
            print(f"    {number}. {option.label}")
            print(f"       {option.hint}")
        print()
        picked = _select(
            GATE_QUESTION,
            choices=[questionary.Choice(o.label, value=str(o.value)) for o in GATE_OPTIONS],
            use_shortcuts=True,
            instruction="(press 1-3, or use arrow keys and Enter)",
        )
        return None if picked is None else RoleGateChoice(picked)

    def _gate(mode: str) -> str:
        try:
            return gate_role_mode(mode, ask=_ask_gate)
        except ConfigureCredentialsFirst:
            print(f"\n  {CONFIGURE_FIRST_INSTRUCTION}")
            print("  Nothing was saved.")
            raise SystemExit(0) from None

    _run_spec_setup(AWS_SETUP, on_mode_chosen=_gate)


def _setup_slack() -> None:
    from integrations.slack.setup import SLACK_SETUP

    _run_spec_setup(SLACK_SETUP)


def _setup_opensearch() -> None:
    from integrations.opensearch.setup import OPENSEARCH_SETUP

    _run_spec_setup(OPENSEARCH_SETUP)


def _setup_servicenow() -> None:
    from integrations.servicenow.setup import SERVICENOW_SETUP

    _run_spec_setup(SERVICENOW_SETUP)


def _setup_rds() -> None:
    from integrations.rds.setup import RDS_SETUP

    _run_spec_setup(RDS_SETUP)


def _setup_tracer() -> None:
    from integrations.tracer.setup import TRACER_SETUP

    _run_spec_setup(TRACER_SETUP)


def _setup_vercel() -> None:
    from integrations.vercel.setup import VERCEL_SETUP

    _run_spec_setup(VERCEL_SETUP)


def _setup_railway() -> None:
    from integrations.railway.setup import RAILWAY_SETUP

    _run_spec_setup(RAILWAY_SETUP)


def _setup_betterstack() -> None:
    from integrations.betterstack.setup import BETTERSTACK_SETUP

    _run_spec_setup(BETTERSTACK_SETUP)


def _setup_incident_io() -> None:
    from integrations.incident_io.setup import INCIDENT_IO_SETUP

    _run_spec_setup(INCIDENT_IO_SETUP)


def _setup_gitlab() -> None:
    from integrations.gitlab.setup import GITLAB_SETUP

    _run_spec_setup(GITLAB_SETUP)


def _setup_sentry() -> None:
    from integrations.sentry.setup import SENTRY_SETUP

    _run_spec_setup(SENTRY_SETUP)


def _setup_posthog() -> None:
    from integrations.posthog.setup import POSTHOG_SETUP

    _run_spec_setup(POSTHOG_SETUP)


def _setup_mongodb() -> None:
    from integrations.mongodb.setup import MONGODB_SETUP

    _run_spec_setup(MONGODB_SETUP)


def _setup_redis() -> None:
    from integrations.redis.setup import REDIS_SETUP

    _run_spec_setup(REDIS_SETUP)


def _setup_discord() -> None:
    from integrations.discord.setup import DISCORD_SETUP

    _run_spec_setup(DISCORD_SETUP)


def _run_spec_setup(
    spec: IntegrationSetupSpec,
    *,
    on_mode_chosen: Callable[[str], str] | None = None,
) -> None:
    """Prompt for a spec's fields, then validate, verify, and persist them.

    Fields are prefilled from the stored credentials so re-running setup is a
    series of enters, not a retype, and never silently drops a value the user
    did not re-type. When the spec declares a picker (``mode_prompt``), only the
    chosen mode's fields are asked; fields belonging to another mode are cleared.

    Each field is checked as it is answered so a blank required value is
    re-asked immediately, rather than surfacing after the user has worked
    through the rest of the prompts.
    """
    from integrations.setup_flow import apply_setup
    from integrations.store import get_integration

    if spec.guide is not None:
        from integrations.setup import run_guided_setup

        try:
            run_guided_setup(spec)
        except (EOFError, KeyboardInterrupt):
            print("\nSetup cancelled.")
            sys.exit(1)
        return

    stored = (get_integration(spec.service) or {}).get("credentials") or {}

    mode: str | None = None
    if spec.mode_prompt:
        mode = _select(
            spec.mode_prompt,
            choices=[questionary.Choice(m.label, value=m.value) for m in spec.modes],
            instruction="(use arrow keys)",
        )
        if mode is None:
            print("\nAborted.")
            sys.exit(1)
        if on_mode_chosen is not None:
            # A vendor may check the mode's prerequisite the moment it is
            # picked and steer to another mode before any credential is typed.
            mode = on_mode_chosen(mode)

    collectable = {field.name for field in spec.collectable_fields(mode)}

    values: dict[str, str | None] = {}
    for field in spec.fields:
        if field.is_constant:
            values[field.name] = field.constant
            continue
        if field.name not in collectable:
            # Gated field for an unchosen mode: clear it rather than prompt, so
            # switching modes turns the other mode's credentials off.
            values[field.name] = ""
            continue
        default = str(stored.get(field.name) or "") or field.default
        # A field with a default is never missing — apply_setup substitutes it —
        # so only a defaultless required field can be blank here. Ask again
        # rather than exit: an empty answer at an interactive prompt is not a
        # fatal error, and Ctrl+C is the way out.
        while True:
            value = _p(field.question, default=default, secret=field.secret)
            if not value and spec.is_required(field, mode) and not field.default:
                print(f"  {field.label} is required. Enter a value, or press Ctrl+C to cancel.")
                continue
            problem = field.validate(value) if value and field.validate else None
            if problem is not None:
                print(f"  {problem}")
                continue
            break
        values[field.name] = value

    print(f"\n  Validating {spec.service} credentials...")
    outcome = apply_setup(spec, values)
    if not outcome.ok:
        print(f"  error: {outcome.detail}", file=sys.stderr)
        print(f"  Nothing was saved. Run `opensre integrations setup {spec.service}` to try again.")
        sys.exit(1)
    print(f"  {outcome.detail}")
    print("  Next:")
    print(f"    - opensre integrations verify {spec.service}")


def _setup_telegram() -> None:
    from integrations.telegram.setup import TELEGRAM_SETUP

    _run_spec_setup(TELEGRAM_SETUP)


def _setup_rocketchat() -> None:
    from integrations.rocketchat.setup import ROCKETCHAT_SETUP

    _run_spec_setup(ROCKETCHAT_SETUP)


def _setup_buzz() -> None:
    from integrations.buzz.setup import BUZZ_SETUP

    _run_spec_setup(BUZZ_SETUP)


def _setup_smtp() -> None:
    from integrations.smtp.setup import SMTP_SETUP

    _run_spec_setup(SMTP_SETUP)


def _setup_whatsapp() -> None:
    from integrations.whatsapp.setup import WHATSAPP_SETUP

    _run_spec_setup(WHATSAPP_SETUP)


def _setup_twilio() -> None:
    """Wizard for the Twilio SMS integration.

    WhatsApp delivery is configured separately via ``setup whatsapp``.
    """
    from integrations.twilio.setup import TWILIO_SETUP

    _run_spec_setup(TWILIO_SETUP)


def _setup_posthog_mcp() -> None:
    from integrations.posthog_mcp.setup import POSTHOG_MCP_SETUP

    _run_spec_setup(POSTHOG_MCP_SETUP)


def _setup_sentry_mcp() -> None:
    from integrations.sentry_mcp.setup import SENTRY_MCP_SETUP

    _run_spec_setup(SENTRY_MCP_SETUP)


def _setup_x_mcp() -> None:
    from integrations.x_mcp.setup import X_MCP_SETUP

    _run_spec_setup(X_MCP_SETUP)


def _setup_mcp_gateway() -> None:
    from integrations.mcp_gateway.setup import MCP_GATEWAY_SETUP

    _run_spec_setup(MCP_GATEWAY_SETUP)


def _setup_postgresql() -> None:
    from integrations.postgresql.setup import POSTGRESQL_SETUP

    _run_spec_setup(POSTGRESQL_SETUP)


def _setup_mysql() -> None:
    from integrations.mysql.setup import MYSQL_SETUP

    _run_spec_setup(MYSQL_SETUP)


def _setup_mongodb_atlas() -> None:
    from integrations.mongodb_atlas.setup import MONGODB_ATLAS_SETUP

    _run_spec_setup(MONGODB_ATLAS_SETUP)


def _setup_mariadb() -> None:
    from integrations.mariadb.setup import MARIADB_SETUP

    _run_spec_setup(MARIADB_SETUP)


def _setup_alertmanager() -> None:
    from integrations.alertmanager.setup import ALERTMANAGER_SETUP

    _run_spec_setup(ALERTMANAGER_SETUP)


def _setup_signoz() -> None:
    from integrations.signoz.setup import SIGNOZ_SETUP

    _run_spec_setup(SIGNOZ_SETUP)


def _setup_jenkins() -> None:
    from integrations.jenkins.setup import JENKINS_SETUP

    _run_spec_setup(JENKINS_SETUP)


def _setup_helm() -> None:
    from integrations.helm.setup import HELM_SETUP

    _run_spec_setup(HELM_SETUP)


def _setup_tempo() -> None:
    from integrations.tempo.setup import TEMPO_SETUP

    _run_spec_setup(TEMPO_SETUP)


def _setup_pagerduty() -> None:
    from integrations.pagerduty.setup import PAGERDUTY_SETUP

    _run_spec_setup(PAGERDUTY_SETUP)


def _setup_kubernetes() -> None:
    from integrations.kubernetes.setup import KUBERNETES_SETUP

    _run_spec_setup(KUBERNETES_SETUP)


def _setup_google_docs() -> None:
    from integrations.google_docs import GOOGLE_DOCS_SETUP

    _run_spec_setup(GOOGLE_DOCS_SETUP)


def _setup_yandex_cloud() -> None:
    from integrations.yandex_cloud.setup import setup_spec_for_this_host

    # Built per call rather than imported as a constant: on a Yandex Cloud VM the
    # folder and cloud ids come from the instance metadata service, and asking
    # for them at import time would cost a timeout on every start elsewhere.
    _run_spec_setup(setup_spec_for_this_host())


_HANDLERS: dict[str, Any] = {
    "alertmanager": _setup_alertmanager,
    "aws": _setup_aws,
    "betterstack": _setup_betterstack,
    "coralogix": _setup_coralogix,
    "datadog": _setup_datadog,
    "groundcover": _setup_groundcover,
    "grafana": _setup_grafana,
    "honeycomb": _setup_honeycomb,
    "helm": _setup_helm,
    "incident_io": _setup_incident_io,
    "mariadb": _setup_mariadb,
    "mongodb_atlas": _setup_mongodb_atlas,
    "slack": _setup_slack,
    "opensearch": _setup_opensearch,
    "rds": _setup_rds,
    "tracer": _setup_tracer,
    "vercel": _setup_vercel,
    "railway": _setup_railway,
    "github": setup_github,
    "gitlab": _setup_gitlab,
    "sentry": _setup_sentry,
    "posthog": _setup_posthog,
    "mongodb": _setup_mongodb,
    "discord": _setup_discord,
    "telegram": _setup_telegram,
    "rocketchat": _setup_rocketchat,
    "buzz": _setup_buzz,
    "smtp": _setup_smtp,
    "whatsapp": _setup_whatsapp,
    "twilio": _setup_twilio,
    "posthog_mcp": _setup_posthog_mcp,
    "sentry_mcp": _setup_sentry_mcp,
    "x_mcp": _setup_x_mcp,
    "mcp_gateway": _setup_mcp_gateway,
    "postgresql": _setup_postgresql,
    "mysql": _setup_mysql,
    "redis": _setup_redis,
    "signoz": _setup_signoz,
    "jenkins": _setup_jenkins,
    "tempo": _setup_tempo,
    "pagerduty": _setup_pagerduty,
    "kubernetes": _setup_kubernetes,
    "servicenow": _setup_servicenow,
    "new_relic": _setup_new_relic,
    "google_docs": _setup_google_docs,
    "yandex_cloud": _setup_yandex_cloud,
}


def _setup_dagster() -> None:
    from integrations.dagster.setup import DAGSTER_SETUP

    _run_spec_setup(DAGSTER_SETUP)


_HANDLERS["dagster"] = _setup_dagster


def _setup_temporal() -> None:
    from integrations.temporal.setup import TEMPORAL_SETUP

    _run_spec_setup(TEMPORAL_SETUP)


_HANDLERS["temporal"] = _setup_temporal


def _setup_azure() -> None:
    from integrations.azure.setup import AZURE_SETUP

    _run_spec_setup(AZURE_SETUP)


_HANDLERS["azure"] = _setup_azure


def _setup_azure_sql() -> None:
    from integrations.azure_sql.setup import AZURE_SQL_SETUP

    _run_spec_setup(AZURE_SQL_SETUP)


_HANDLERS["azure_sql"] = _setup_azure_sql


def setup_services() -> tuple[str, ...]:
    """Return the services that both declare a setup order and have a handler.

    Computed per call rather than once at import: a plugin registers its spec
    and adds its ``_HANDLERS`` entry after this module has been imported, and a
    snapshot taken here would reject it as unsupported for the rest of the
    process.
    """
    return tuple(service for service in SUPPORTED_SETUP_SERVICES if service in _HANDLERS)


def cmd_setup(service: str | None) -> str:
    available = setup_services()
    if not service:
        try:
            service = _select(
                "Which service would you like to set up?",
                choices=list(available),
                instruction="(use arrow keys)",
            )
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
    if service:
        service = resolve_management_service(service)
    if not service or service not in available:
        _die(f"Usage: setup <service>. Supported: {', '.join(available)}")
    print(f"\n  Setting up {_B}{service}{_R}\n")
    _HANDLERS[service]()
    print(f"\n  {GLYPH_SUCCESS} Saved → {resolve_store_path()}\n")
    return service


def _remote_integration_records() -> list[dict[str, Any]]:
    """The signed-in account's org integrations; empty when signed out/offline."""
    from integrations.account_integrations import load_account_integrations

    return load_account_integrations()


def _remote_integration(service: str) -> dict[str, Any] | None:
    """The active remote record for ``service``, or ``None``."""
    for record in _remote_integration_records():
        if record.get("service") == service and record.get("status") == "active":
            return record
    return None


def cmd_list() -> None:
    from infrastructure.process.runtime_flags import is_json_output

    local_items = [{**item, "source": "local"} for item in list_integrations()]
    remote_services = {str(record.get("service") or "") for record in _remote_integration_records()}
    # A service connected in the web app shows once, as remote — that is the
    # record runtime resolution uses when both exist.
    items = [item for item in local_items if item["service"] not in remote_services] + [
        {
            "service": record.get("service"),
            "status": record.get("status"),
            "id": record.get("id"),
            "instance_names": [
                inst.get("name", "default")
                for inst in record.get("instances", [])
                if isinstance(inst, dict)
            ],
            "source": "remote",
        }
        for record in _remote_integration_records()
    ]

    if is_json_output():
        _json_echo(items)
        return

    if not items:
        print(
            "  No integrations. Run: opensre integrations setup <service>, "
            "or opensre onboard for the guided wizard."
        )
        return

    from rich.markup import escape

    from infrastructure.terminal.theme import HIGHLIGHT, SECONDARY, TEXT
    from integrations._table_render import new_table, render_table

    table = new_table()
    table.add_column("SERVICE", style=TEXT, no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("SOURCE", style=SECONDARY, no_wrap=True)
    table.add_column("ID", style=SECONDARY)
    for i in items:
        status = i["status"]
        status_cell = (
            f"[bold {HIGHLIGHT}]{GLYPH_SUCCESS} {escape(status)}[/]"
            if status == "active"
            else escape(str(status))
        )
        table.add_row(
            escape(str(i["service"])),
            status_cell,
            escape(str(i["source"])),
            escape(str(i["id"])),
        )

    print(render_table(table))


def cmd_show(service: str | None) -> None:
    if not service:
        _die("Usage: show <service>")
        return
    service = resolve_management_service(service)
    remote_record = _remote_integration(service)
    if remote_record is not None:
        _json_echo(_mask({**remote_record, "source": "remote"}))
        return
    record = get_integration(service)
    if not record:
        _die(f"No active integration for '{service}'.")
        return
    _json_echo(_mask({**record, "source": "local"}))


def cmd_remove(service: str | None) -> None:
    from infrastructure.process.runtime_flags import is_yes

    if not service:
        _die("Usage: remove <service>")
        return
    service = resolve_management_service(service)
    if not is_yes():
        try:
            confirmed = _confirm(f"Remove '{service}'?", default=False)
        except (EOFError, KeyboardInterrupt):
            return
        if not confirmed:
            print("  Cancelled.")
            return
    if remove_integration(service):
        delete_webapp_org_integration(service)
        print(f"  {GLYPH_SUCCESS} Removed '{service}'.")
    else:
        print(f"  No integration found for '{service}'.")
    if _remote_integration(service) is not None:
        print(
            f"  {service} is still connected in the OpenSRE app and stays active here; "
            "disconnect it at app.opensre.com → Integrations to remove it."
        )


def cmd_verify(service: str | None, *, send_slack_test: bool = False) -> int:
    from infrastructure.process.runtime_flags import is_json_output

    if service:
        service = resolve_management_service(service)
    if service and service not in SUPPORTED_VERIFY_SERVICES:
        _die(f"Usage: verify [service]. Supported: {', '.join(SUPPORTED_VERIFY_SERVICES)}")

    results = verify_integrations(service=service, send_slack_test=send_slack_test)

    if is_json_output():
        _json_echo(results)
    else:
        print(format_verification_results(results))
    return verification_exit_code(results, requested_service=service)
