"""Interactive CLI for managing local integrations (~/.tracer/integrations.json).

Usage:
    python -m app.integrations setup <service>
    python -m app.integrations list
    python -m app.integrations show <service>
    python -m app.integrations remove <service>
    python -m app.integrations verify [service] [--send-slack-test]

Supported services: aws, coralogix, datadog, grafana, honeycomb, mariadb, discord, mongodb, mongodb_atlas, postgresql, slack, opensearch, rds, tracer, github, sentry, vercel
"""

from __future__ import annotations

import json
import sys
from typing import Any, NoReturn

import questionary

from app.integrations.gitlab import DEFAULT_GITLAB_BASE_URL
from app.integrations.store import (
    STORE_PATH,
    get_integration,
    list_integrations,
    remove_integration,
    upsert_integration,
)
from app.integrations.verify import (
    SUPPORTED_VERIFY_SERVICES,
    format_verification_results,
    verification_exit_code,
    verify_integrations,
)

_B = "\033[1m"
_R = "\033[0m"


def _json_echo(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


_SECRET_KEYS = frozenset({
    "api_token",
    "api_key",
    "api_private_key",
    "app_key",
    "bot_token",
    "password",
    "secret_access_key",
    "session_token",
    "jwt_token",
    "webhook_url",
    "auth_token",
    "connection_string",
})


def _p(label: str, default: str = "", secret: bool = False) -> str:
    try:
        if secret:
            result = questionary.password(f"  {label}").ask()
        else:
            result = questionary.text(f"  {label}", default=default).ask()
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
    endpoint = _p("Instance URL (e.g. https://myorg.grafana.net)")
    api_key = _p("Service account token", secret=True)
    if not endpoint or not api_key:
        _die("endpoint and api_key are required.")
    upsert_integration("grafana", {"credentials": {"endpoint": endpoint, "api_key": api_key}})


def _setup_datadog() -> None:
    api_key = _p("API key", secret=True)
    app_key = _p("Application key", secret=True)
    site = _p("Site", default="datadoghq.com")
    if not api_key or not app_key:
        _die("api_key and app_key are required.")
    upsert_integration(
        "datadog", {"credentials": {"api_key": api_key, "app_key": app_key, "site": site}}
    )


def _setup_honeycomb() -> None:
    api_key = _p("Configuration API key", secret=True)
    dataset = _p("Dataset slug or __all__", default="__all__")
    base_url = _p("API URL", default="https://api.honeycomb.io")
    if not api_key:
        _die("api_key is required.")
    upsert_integration(
        "honeycomb",
        {"credentials": {"api_key": api_key, "dataset": dataset, "base_url": base_url}},
    )


def _setup_coralogix() -> None:
    api_key = _p("DataPrime API key", secret=True)
    base_url = _p("API URL", default="https://api.coralogix.com")
    application_name = _p("Application name (optional)")
    subsystem_name = _p("Subsystem name (optional)")
    if not api_key or not base_url:
        _die("api_key and base_url are required.")
    upsert_integration(
        "coralogix",
        {
            "credentials": {
                "api_key": api_key,
                "base_url": base_url,
                "application_name": application_name,
                "subsystem_name": subsystem_name,
            }
        },
    )


def _setup_aws() -> None:
    choice = questionary.select(
        "AWS authentication method:",
        choices=[
            questionary.Choice("IAM Role ARN", value="1"),
            questionary.Choice("Access Key + Secret", value="2"),
        ],
        instruction="(use arrow keys)",
    ).ask()
    if choice is None:
        print("\nAborted.")
        sys.exit(1)
    region = _p("Region", default="us-east-1")
    if choice == "1":
        role_arn = _p("IAM Role ARN")
        if not role_arn:
            _die("role_arn is required.")
        upsert_integration(
            "aws",
            {
                "role_arn": role_arn,
                "external_id": _p("External ID (optional)"),
                "credentials": {"region": region},
            },
        )
    else:
        access_key = _p("AWS_ACCESS_KEY_ID", secret=True)
        secret_key = _p("AWS_SECRET_ACCESS_KEY", secret=True)
        if not access_key or not secret_key:
            _die("access_key and secret_key are required.")
        upsert_integration(
            "aws",
            {
                "credentials": {
                    "access_key_id": access_key,
                    "secret_access_key": secret_key,
                    "session_token": _p("Session token (optional)"),
                    "region": region,
                }
            },
        )


def _setup_slack() -> None:
    webhook_url = _p("Slack webhook URL", secret=True)
    if not webhook_url:
        _die("webhook_url is required.")
    upsert_integration("slack", {"credentials": {"webhook_url": webhook_url}})


def _setup_opensearch() -> None:
    endpoint = _p("Endpoint (e.g. https://my-cluster.us-east-1.es.amazonaws.com)")
    creds: dict[str, Any] = {"endpoint": endpoint}
    auth_choice = questionary.select(
        "OpenSearch authentication method:",
        choices=[
            questionary.Choice("Username + Password", value="1"),
            questionary.Choice("API key", value="2"),
        ],
        instruction="(use arrow keys)",
    ).ask()
    if auth_choice is None:
        print("\nAborted.")
        sys.exit(1)
    if auth_choice == "2":
        creds["api_key"] = _p("API key", secret=True)
    else:
        creds["username"] = _p("Username", default="admin")
        creds["password"] = _p("Password", secret=True)
    upsert_integration("opensearch", {"credentials": creds})


def _setup_rds() -> None:
    host = _p("Host (e.g. mydb.xxxx.us-east-1.rds.amazonaws.com)")
    port = _p("Port", default="5432")
    database = _p("Database name")
    username = _p("Username")
    password = _p("Password", secret=True)
    if not host or not database or not username:
        _die("host, database, and username are required.")
    upsert_integration(
        "rds",
        {
            "credentials": {
                "host": host,
                "port": int(port) if port.isdigit() else 5432,
                "database": database,
                "username": username,
                "password": password,
            }
        },
    )


def _setup_tracer() -> None:
    base_url = _p("Tracer web app URL", default="http://localhost:3000")
    jwt_token = _p("JWT token", secret=True)
    if not base_url or not jwt_token:
        _die("base_url and jwt_token are required.")
    upsert_integration("tracer", {"credentials": {"base_url": base_url, "jwt_token": jwt_token}})


def _setup_vercel() -> None:
    api_token = _p("Vercel API token", secret=True)
    team_id = _p("Team ID (optional for personal accounts)")
    if not api_token:
        _die("api_token is required.")
    upsert_integration("vercel", {"credentials": {"api_token": api_token, "team_id": team_id}})


def _setup_github() -> None:
    print("  1) SSE  2) Streamable HTTP  3) stdio")
    choice = _p("Choice", default="2")
    mode = {"1": "sse", "2": "streamable-http", "3": "stdio"}.get(choice, "streamable-http")
    credentials: dict[str, Any] = {"mode": mode}
    if mode == "stdio":
        command = _p("Command", default="github-mcp-server")
        args = _p("Args", default="stdio --toolsets repos,issues,pull_requests,actions")
        if not command:
            _die("command is required for stdio mode.")
        credentials["command"] = command
        credentials["args"] = [part for part in args.split() if part]
    else:
        url = _p("MCP URL", default="https://api.githubcopilot.com/mcp/")
        if not url:
            _die("url is required for remote MCP modes.")
        credentials["url"] = url
    credentials["auth_token"] = _p(
        "GitHub PAT / auth token (optional if the server authenticates upstream)",
        secret=True,
    )
    toolsets = _p("Toolsets", default="repos,issues,pull_requests,actions")
    credentials["toolsets"] = [part.strip() for part in toolsets.split(",") if part.strip()]
    upsert_integration("github", {"credentials": credentials})

def _setup_gitlab() -> None:
    base_url = _p("Gitlab base URL", default=DEFAULT_GITLAB_BASE_URL)
    auth_token = _p("Gitlab access token", secret=True)
    upsert_integration(
        "gitlab",
        {
            "credentials": {
                "base_url": base_url,
                "auth_token": auth_token
            }
        },
    )


def _setup_sentry() -> None:
    base_url = _p("Sentry URL", default="https://sentry.io")
    organization_slug = _p("Organization slug")
    auth_token = _p("Auth token", secret=True)
    project_slug = _p("Project slug (optional)")
    if not organization_slug or not auth_token:
        _die("organization_slug and auth_token are required.")
    upsert_integration(
        "sentry",
        {
            "credentials": {
                "base_url": base_url,
                "organization_slug": organization_slug,
                "auth_token": auth_token,
                "project_slug": project_slug,
            }
        },
    )


def _setup_mongodb() -> None:
    connection_string = _p(
        "Connection string (e.g. mongodb+srv://user:pass@cluster.example.net)", secret=True
    )
    database = _p("Database name")
    auth_source = _p("Auth source", default="admin")
    tls_choice = questionary.select(
        "TLS enabled?",
        choices=[
            questionary.Choice("Yes", value="1"),
            questionary.Choice("No", value="0"),
        ],
        instruction="(use arrow keys)",
    ).ask()
    if tls_choice is None:
        print("\nAborted.")
        sys.exit(1)
    tls = tls_choice == "1"
    if not connection_string:
        _die("connection_string is required.")
    upsert_integration(
        "mongodb",
        {
            "credentials": {
                "connection_string": connection_string,
                "database": database,
                "auth_source": auth_source,
                "tls": tls,
            }
        },
    )

def _register_discord_slash_command(application_id: str, bot_token: str) -> None:
    import httpx

    url = f"https://discord.com/api/v10/applications/{application_id}/commands"
    payload = {
        "name": "investigate",
        "description": "Trigger an OpenSRE investigation",
        "options": [
            {"name": "alert", "description": "Alert JSON or description", "type": 3, "required": True}
        ],
    }
    resp = httpx.put(url, json=[payload], headers={"Authorization": f"Bot {bot_token}"}, timeout=10)
    if resp.is_success:
        print("  ✓ /investigate slash command registered.")
    else:
        print(f"  ⚠ Slash command registration failed ({resp.status_code}): {resp.text}")


def _setup_discord() -> None:
    bot_token = _p("Discord bot token", secret=True)
    application_id = _p("Discord application ID")
    public_key = _p("Discord public key (from Developer Portal)")
    default_channel_id = _p("Default channel ID (optional)")
    upsert_integration("discord", {"credentials": {
        "bot_token": bot_token,
        "application_id": application_id,
        "public_key": public_key,
        "default_channel_id": default_channel_id,
    }})
    _register_discord_slash_command(application_id, bot_token)


def _setup_postgresql() -> None:
    host = _p("Host (e.g. localhost or postgres.example.com)")
    database = _p("Database name")
    if not host or not database:
        _die("host and database are required.")
    port = _p("Port", default="5432")
    username = _p("Username", default="postgres")
    password = _p("Password", secret=True)
    ssl_mode_choice = questionary.select(
        "SSL mode",
        choices=[
            questionary.Choice("prefer (recommended)", value="prefer"),
            questionary.Choice("require", value="require"),
            questionary.Choice("disable", value="disable"),
        ],
        instruction="(use arrow keys)",
    ).ask()
    if ssl_mode_choice is None:
        print("\nAborted.")
        sys.exit(1)
    upsert_integration(
        "postgresql",
        {
            "credentials": {
                "host": host,
                "port": int(port) if port.isdigit() else 5432,
                "database": database,
                "username": username or "postgres",
                "password": password,
                "ssl_mode": ssl_mode_choice,
            }
        },
    )


def _setup_mysql() -> None:
    host = _p("Host (e.g. localhost or mysql.example.com)")
    database = _p("Database name")
    if not host or not database:
        _die("host and database are required.")
    port = _p("Port", default="3306")
    username = _p("Username", default="root")
    password = _p("Password", secret=True)
    ssl_mode_choice = questionary.select(
        "SSL mode",
        choices=[
            questionary.Choice("preferred (encrypted, no cert verification)", value="preferred"),
            questionary.Choice("required", value="required"),
            questionary.Choice("disabled", value="disabled"),
        ],
        instruction="(use arrow keys)",
    ).ask()
    if ssl_mode_choice is None:
        print("\nAborted.")
        sys.exit(1)
    upsert_integration(
        "mysql",
        {
            "credentials": {
                "host": host,
                "port": int(port) if port.isdigit() else 3306,
                "database": database,
                "username": username or "root",
                "password": password,
                "ssl_mode": ssl_mode_choice,
            }
        },
    )


def _setup_mongodb_atlas() -> None:
    api_public_key = _p("Atlas API public key")
    api_private_key = _p("Atlas API private key", secret=True)
    project_id = _p("Atlas project ID (group ID)")
    base_url = _p("Atlas API base URL", default="https://cloud.mongodb.com/api/atlas/v2")
    if not api_public_key or not api_private_key or not project_id:
        _die("api_public_key, api_private_key, and project_id are required.")
    upsert_integration(
        "mongodb_atlas",
        {
            "credentials": {
                "api_public_key": api_public_key,
                "api_private_key": api_private_key,
                "project_id": project_id,
                "base_url": base_url,
            }
        },
    )


def _setup_mariadb() -> None:
    host = _p("Host (e.g. db.example.com)")
    port = _p("Port", default="3306")
    database = _p("Database name")
    username = _p("Username")
    password = _p("Password", secret=True)
    ssl_choice = questionary.select(
        "SSL enabled?",
        choices=[
            questionary.Choice("Yes", value="1"),
            questionary.Choice("No", value="0"),
        ],
        instruction="(use arrow keys)",
    ).ask()
    if ssl_choice is None:
        print("\nAborted.")
        sys.exit(1)
    ssl = ssl_choice == "1"
    if not host or not database or not username:
        _die("host, database, and username are required.")
    upsert_integration(
        "mariadb",
        {
            "credentials": {
                "host": host,
                "port": _parse_port(port),
                "database": database,
                "username": username,
                "password": password,
                "ssl": ssl,
            }
        },
    )


_HANDLERS: dict[str, Any] = {
    "aws": _setup_aws,
    "coralogix": _setup_coralogix,
    "datadog": _setup_datadog,
    "grafana": _setup_grafana,
    "honeycomb": _setup_honeycomb,
    "mariadb": _setup_mariadb,
    "mongodb_atlas": _setup_mongodb_atlas,
    "slack": _setup_slack,
    "opensearch": _setup_opensearch,
    "rds": _setup_rds,
    "tracer": _setup_tracer,
    "vercel": _setup_vercel,
    "github": _setup_github,
    "gitlab": _setup_gitlab,
    "sentry": _setup_sentry,
    "mongodb": _setup_mongodb,
    "discord": _setup_discord,
    "postgresql": _setup_postgresql,
    "mysql": _setup_mysql,
}

SUPPORTED = ", ".join(_HANDLERS)
SUPPORTED_VERIFY = ", ".join(SUPPORTED_VERIFY_SERVICES)


def cmd_setup(service: str | None) -> str:
    if not service:
        try:
            service = questionary.select(
                "Which service would you like to set up?",
                choices=sorted(_HANDLERS),
                instruction="(use arrow keys)",
            ).ask()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
    if not service or service not in _HANDLERS:
        _die(f"Usage: setup <service>. Supported: {SUPPORTED}")
    print(f"\n  Setting up {_B}{service}{_R}\n")
    _HANDLERS[service]()
    print(f"\n  ✓ Saved → {STORE_PATH}\n")
    return service


def cmd_list() -> None:
    from app.cli.context import is_json_output

    items = list_integrations()

    if is_json_output():
        _json_echo(items)
        return

    if not items:
        print("  No integrations. Run: python -m app.integrations setup <service>")
        return
    print(f"\n  {_B}{'SERVICE':<14}STATUS    ID{_R}")
    for i in items:
        print(f"  {i['service']:<14}{i['status']:<10}{i['id']}")
    print()


def cmd_show(service: str | None) -> None:
    if not service:
        _die("Usage: show <service>")
        return
    record = get_integration(service)
    if not record:
        _die(f"No active integration for '{service}'.")
        return
    _json_echo(_mask(record))


def cmd_remove(service: str | None) -> None:
    from app.cli.context import is_yes

    if not service:
        _die("Usage: remove <service>")
        return
    if not is_yes():
        try:
            confirmed = questionary.confirm(f"  Remove '{service}'?", default=False).ask()
        except (EOFError, KeyboardInterrupt):
            return
        if not confirmed:
            print("  Cancelled.")
            return
    if remove_integration(service):
        print(f"  ✓ Removed '{service}'.")
    else:
        print(f"  No integration found for '{service}'.")


def cmd_verify(
    service: str | None,
    *,
    send_slack_test: bool = False,
) -> int:
    from app.cli.context import is_json_output

    if service and service not in SUPPORTED_VERIFY_SERVICES:
        _die(f"Usage: verify [service]. Supported: {SUPPORTED_VERIFY}")

    results = verify_integrations(
        service=service,
        send_slack_test=send_slack_test,
    )

    if is_json_output():
        _json_echo(results)
    else:
        print(format_verification_results(results))
    return int(verification_exit_code(results, requested_service=service))
