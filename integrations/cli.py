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
from typing import TYPE_CHECKING, Any, NoReturn, cast

import questionary

from infrastructure.terminal.prompt_support import (
    QUESTIONARY_QMARK,
    questionary_prompt_style,
)
from infrastructure.terminal.theme import (
    ANSI_BOLD,
    ANSI_DIM,
    ANSI_RESET,
    DEVICE_CODE_ANSI,
    GLYPH_SUCCESS,
)

if TYPE_CHECKING:
    from integrations.github.mcp import GitHubMcpDisplayDetailLevel
    from integrations.setup_flow import IntegrationSetupSpec

from integrations.registry import SUPPORTED_SETUP_SERVICES, resolve_management_service
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
_DIM = ANSI_DIM


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


def _select(message: str, choices: list[Any], **kwargs: Any) -> Any:
    return questionary.select(
        message,
        choices=choices,
        qmark=QUESTIONARY_QMARK,
        style=questionary_prompt_style(),
        **kwargs,
    ).ask()


def _confirm(message: str, **kwargs: Any) -> Any:
    return questionary.confirm(
        message, qmark=QUESTIONARY_QMARK, style=questionary_prompt_style(), **kwargs
    ).ask()


def _p(label: str, default: str = "", secret: bool = False) -> str:
    try:
        if secret:
            result = questionary.password(
                label, qmark=QUESTIONARY_QMARK, style=questionary_prompt_style()
            ).ask()
        else:
            result = questionary.text(
                label, default=default, qmark=QUESTIONARY_QMARK, style=questionary_prompt_style()
            ).ask()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    if result is None:
        print("\nAborted.")
        sys.exit(1)
    return result.strip() or default


def _die(msg: str) -> NoReturn:
    print(f"  error: {msg}", file=sys.stderr)
    sys.exit(1)


def _prompt_github_repo_report_level() -> GitHubMcpDisplayDetailLevel:
    """Ask how much repository access detail to print after a successful validation."""

    try:
        sel = _select(
            "How much repository detail should we show?",
            choices=[
                questionary.Choice("Brief (recommended) — no repo names", value="summary"),
                questionary.Choice("Standard — scope summary only", value="standard"),
                questionary.Choice("Expanded — include repo names", value="full"),
            ],
            default="summary",
        )
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    if sel is None:
        return "summary"
    if sel in ("summary", "standard", "full"):
        from integrations.github.mcp import GitHubMcpDisplayDetailLevel as _Detail

        return cast(_Detail, sel)
    return "summary"


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


def _github_browser_authorize() -> str | None:
    """Run GitHub device-flow browser authorization.

    Returns the access token, or ``None`` when the flow is unavailable so the
    caller can fall back to manual token entry.
    """
    from integrations.github.mcp_oauth import (
        GitHubDeviceCode,
        GitHubDeviceFlowError,
        authorize_github_via_device_flow,
    )

    def _show(code: GitHubDeviceCode) -> None:
        print()
        print(f"  1. Your browser will open {code.verification_uri}")
        print("     (if it doesn't open automatically, visit that URL yourself).")
        print(
            f"  2. Enter this one-time code when GitHub asks: {DEVICE_CODE_ANSI}{code.user_code}{_R}"
        )
        print("  3. Approve the request for OpenSRE.")
        print()
        print(f"  {_DIM}Waiting for you to approve in the browser… (Ctrl-C to cancel){_R}")

    print()
    print("  Sign in to GitHub in your browser (device authorization):")
    print(f"  {_DIM}Requesting a one-time code from GitHub…{_R}")
    try:
        token = authorize_github_via_device_flow(on_prompt=_show)
    except GitHubDeviceFlowError as err:
        print(f"  Browser authorization unavailable: {err}", file=sys.stderr)
        return None
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    except Exception as err:  # network/transport issues
        print(f"  Browser authorization failed: {err}", file=sys.stderr)
        return None
    print(f"  {_B}Authorized.{_R} Saved a GitHub token from the browser sign-in.")
    return token.access_token


def _github_browser_auth_token() -> str:
    """Authorize in the browser, falling back to manual token entry."""
    token = _github_browser_authorize()
    if token:
        return token
    print("  Falling back to manual token entry.")
    return _p("GitHub PAT / auth token", secret=True)


def _setup_github_auth_token(mode: str) -> str:
    """Resolve a GitHub MCP auth token, offering browser sign-in for remote modes."""
    if mode == "stdio":
        return _p(
            "GitHub PAT / auth token (optional if the server authenticates upstream)",
            secret=True,
        )

    auth_method = _select(
        "How do you want to connect OpenSRE to GitHub?",
        choices=[
            questionary.Choice(
                "Sign in with GitHub in your browser (opens a page, enter a one-time code)",
                value="browser",
            ),
            questionary.Choice("Paste a personal access token (PAT)", value="token"),
            questionary.Choice("Skip — the MCP server authenticates upstream", value="none"),
        ],
        default="browser",
    )
    if auth_method is None:
        print("\nAborted.")
        sys.exit(1)
    if auth_method == "none":
        return ""
    if auth_method == "browser":
        return _github_browser_auth_token()
    return _p("GitHub PAT / auth token", secret=True)


def _github_advanced_setup(credentials: dict[str, Any]) -> tuple[str, str]:
    """Prompt the advanced GitHub MCP knobs and return (repo_view, repo_visibility).

    Mutates ``credentials`` in place with mode/url/command/args/auth_token/toolsets.
    """
    from integrations.github.mcp import (
        DEFAULT_GITHUB_MCP_TOOLSETS,
        DEFAULT_GITHUB_MCP_URL,
    )

    # Transport is fixed to Streamable HTTP. In practice it is the only mode anyone
    # selects, and SSE/stdio are deprecated for the hosted GitHub MCP server. The
    # transport prompt was removed on purpose — do NOT reintroduce a transport
    # selection or a stdio branch here.
    mode = "streamable-http"
    credentials["mode"] = mode
    url = _p("MCP URL", default=DEFAULT_GITHUB_MCP_URL)
    if not url:
        _die("url is required for remote MCP modes.")
    credentials["url"] = url
    credentials["auth_token"] = _setup_github_auth_token(mode)
    toolsets = _p("Toolsets", default=",".join(DEFAULT_GITHUB_MCP_TOOLSETS))
    credentials["toolsets"] = [part.strip() for part in toolsets.split(",") if part.strip()]

    repo_view = _select(
        "Which repository view should we use to verify access?",
        choices=[
            questionary.Choice("Auto (recommended)", value="auto"),
            questionary.Choice("Your repositories", value="user"),
            questionary.Choice("Accessible repositories", value="accessible"),
            questionary.Choice("Starred repositories", value="starred"),
            questionary.Choice("Search: user:<your_login>", value="search_user"),
        ],
        default="auto",
    )
    if repo_view is None:
        print("\nAborted.")
        sys.exit(1)
    repo_visibility = _select(
        "Filter repositories by visibility (best-effort)",
        choices=[
            questionary.Choice("Any (recommended)", value="any"),
            questionary.Choice("Public only", value="public"),
            questionary.Choice("Private only", value="private"),
        ],
        default="any",
    )
    if repo_visibility is None:
        print("\nAborted.")
        sys.exit(1)
    return repo_view, repo_visibility


def _setup_github() -> str | None:
    """Configure + validate + save the GitHub MCP integration.

    Returns the authenticated GitHub login on success (``None`` if the validated
    result carried no login), so callers like the first-launch gate can propagate
    the username. Exits the process on validation failure.

    Collection stays custom (browser OAuth + optional repo-scope probes). Persist
    goes through :func:`integrations.setup_flow.apply_setup` so the token lands
    in the keyring and the non-secrets in ``.env``, not just the store.
    """
    import dataclasses

    from integrations.github.mcp import (
        DEFAULT_GITHUB_MCP_MODE,
        DEFAULT_GITHUB_MCP_TOOLSETS,
        DEFAULT_GITHUB_MCP_URL,
        GitHubMcpDisplayDetailLevel,
        GitHubMcpRepoView,
        GitHubMcpRepoVisibilityFilter,
        build_github_mcp_config,
        format_github_mcp_validation_cli_report,
        print_github_mcp_validation_report,
        validate_github_mcp_config,
    )
    from integrations.github.setup import GITHUB_SETUP
    from integrations.setup_flow import apply_setup

    print("  Connect OpenSRE to your GitHub repositories.")
    setup_path = _select(
        "How would you like to connect?",
        choices=[
            questionary.Choice("Sign in with GitHub (recommended)", value="recommended"),
            questionary.Choice("Customize connection", value="customize"),
        ],
        default="recommended",
    )
    if setup_path is None:
        print("\nAborted.")
        sys.exit(1)
    customize = setup_path == "customize"

    credentials: dict[str, Any] = {}
    repo_view: str = "auto"
    repo_visibility: str = "any"

    if customize:
        repo_view, repo_visibility = _github_advanced_setup(credentials)
    else:
        credentials["mode"] = DEFAULT_GITHUB_MCP_MODE
        credentials["url"] = DEFAULT_GITHUB_MCP_URL
        credentials["auth_token"] = _github_browser_auth_token()
        credentials["toolsets"] = list(DEFAULT_GITHUB_MCP_TOOLSETS)

    print("\n  Validating GitHub MCP integration...")
    mcp_config = build_github_mcp_config(credentials)
    result = validate_github_mcp_config(
        mcp_config,
        repo_view=cast(GitHubMcpRepoView, repo_view),
        repo_visibility=cast(GitHubMcpRepoVisibilityFilter, repo_visibility),
    )
    if result.ok:
        # The simple path stays concise: identity + tool availability, no repo dump.
        # Only the advanced path offers the verbose repo listing.
        level = (
            _prompt_github_repo_report_level()
            if customize
            else cast(GitHubMcpDisplayDetailLevel, "summary")
        )
        print()
        print_github_mcp_validation_report(result, detail_level=level)
    else:
        for line in format_github_mcp_validation_cli_report(result).splitlines():
            print(f"  {line}")
        sys.exit(1)

    toolsets = credentials.get("toolsets") or []
    if isinstance(toolsets, str):
        toolsets_value = toolsets
    else:
        toolsets_value = ",".join(str(part).strip() for part in toolsets if str(part).strip())

    # Already verified above (with optional repo-scope probes). Skip the spec's
    # simpler probe so we do not hit the hosted server twice.
    outcome = apply_setup(
        dataclasses.replace(GITHUB_SETUP, verify=None),
        {
            "mode": str(credentials.get("mode") or DEFAULT_GITHUB_MCP_MODE),
            "url": str(credentials.get("url") or DEFAULT_GITHUB_MCP_URL),
            "auth_token": str(credentials.get("auth_token") or ""),
            "toolsets": toolsets_value,
            "username": result.authenticated_user or "",
        },
    )
    if not outcome.ok:
        _die(outcome.detail)
    return result.authenticated_user


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
    "github": _setup_github,
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


def _setup_observe() -> None:
    from integrations.observe.setup import OBSERVE_SETUP

    _run_spec_setup(OBSERVE_SETUP)


_HANDLERS["observe"] = _setup_observe


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


def cmd_list() -> None:
    from infrastructure.process.runtime_flags import is_json_output

    items = list_integrations()

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
    table.add_column("ID", style=SECONDARY)
    for i in items:
        status = i["status"]
        status_cell = (
            f"[bold {HIGHLIGHT}]{GLYPH_SUCCESS} {escape(status)}[/]"
            if status == "active"
            else escape(status)
        )
        table.add_row(escape(i["service"]), status_cell, escape(i["id"]))

    print(render_table(table))


def cmd_show(service: str | None) -> None:
    if not service:
        _die("Usage: show <service>")
        return
    service = resolve_management_service(service)
    record = get_integration(service)
    if not record:
        _die(f"No active integration for '{service}'.")
        return
    _json_echo(_mask(record))


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
