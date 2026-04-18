"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

import questionary
from rich.console import Console
from rich.text import Text

from app.cli.wizard.config import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS
from app.cli.wizard.env_sync import sync_env_values, sync_provider_env
from app.cli.wizard.integration_health import IntegrationHealthResult
from app.cli.wizard.probes import ProbeResult, probe_local_target, probe_remote_target
from app.cli.wizard.prompts import select as select_prompt
from app.cli.wizard.store import get_store_path, load_local_config, save_local_config
from app.integrations.store import get_integration, remove_integration, upsert_integration
from app.llm_credentials import has_llm_api_key, save_llm_api_key

_console = Console()
DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
DEFAULT_GITHUB_MCP_MODE = "streamable-http"
DEFAULT_OPENCLAW_MCP_URL = "http://127.0.0.1:18789/"
DEFAULT_OPENCLAW_MCP_MODE = "stdio"
DEFAULT_OPENCLAW_MCP_COMMAND = "openclaw"
DEFAULT_OPENCLAW_MCP_ARGS = ("mcp", "serve")
DEFAULT_SENTRY_URL = "https://sentry.io"
DEFAULT_GITLAB_BASE_URL = "https://gitlab.com/api/v4"
_ASCII_HEADER = """\
  ___  ____  _____ _   _ ____  ____  _____
 / _ \\|  _ \\| ____| \\ | / ___||  _ \\| ____|
| | | | |_) |  _| |  \\| \\___ \\| |_) |  _|
| |_| |  __/| |___| |\\  |___) |  _ <| |___
 \\___/|_|   |_____|_| \\_|____/|_| \\_\\_____|"""


def build_demo_action_response():
    from app.cli.wizard.validation import build_demo_action_response as _build

    return _build()


def validate_grafana_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_grafana_integration as _validate

    return _validate(**kwargs)


def validate_datadog_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_datadog_integration as _validate

    return _validate(**kwargs)


def validate_honeycomb_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_honeycomb_integration as _validate

    return _validate(**kwargs)


def validate_coralogix_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_coralogix_integration as _validate

    return _validate(**kwargs)


def validate_slack_webhook(**kwargs):
    from app.cli.wizard.integration_health import validate_slack_webhook as _validate

    return _validate(**kwargs)


def validate_aws_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_aws_integration as _validate

    return _validate(**kwargs)


def validate_github_mcp_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_github_mcp_integration as _validate

    return _validate(**kwargs)


def validate_gitlab_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_gitlab_integration as _validate

    return _validate(**kwargs)


def validate_sentry_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_sentry_integration as _validate

    return _validate(**kwargs)


def _looks_like_openclaw_control_ui_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return False

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    return port == 18789 and parsed.path.rstrip("/") == ""


def validate_notion_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_notion_integration as _validate

    return _validate(**kwargs)


def validate_jira_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_jira_integration as _validate

    return _validate(**kwargs)


def validate_google_docs_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_google_docs_integration as _validate

    return _validate(**kwargs)


def validate_vercel_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_vercel_integration as _validate

    return _validate(**kwargs)


def validate_opsgenie_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_opsgenie_integration as _validate

    return _validate(**kwargs)


def validate_discord_bot(**kwargs):
    from app.cli.wizard.integration_health import validate_discord_bot as _validate

    return _validate(**kwargs)


def validate_openclaw_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_openclaw_integration as _validate

    return _validate(**kwargs)


def get_sentry_auth_recommendations():
    from app.integrations.sentry import get_sentry_auth_recommendations as _get

    return _get()

_STYLE = questionary.Style(
    [
        ("qmark", "fg:#5c7cfa bold"),
        ("question", "fg:#f8f9fa bold"),
        ("answer", "fg:#ffd166 bold"),
        ("pointer", "fg:#ffd166 bold"),
        ("highlighted", "fg:#0b1020 bg:#ffd166 bold"),
        ("selected", "fg:#f8f9fa bg:default bold"),
        ("separator", "fg:#74c0fc"),
        ("text", "fg:#d9dee7 bg:default"),
        ("disabled", "fg:#6c757d bg:default italic"),
        ("instruction", "fg:#858585 italic"),
    ]
)


@dataclass(frozen=True)
class Choice:
    """A selectable wizard choice."""

    value: str
    label: str
    group: str | None = None
    hint: str | None = None


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string_value(value: object, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _joined_values(value: object, *, separator: str, fallback: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return separator.join(value)
    return fallback


def _local_defaults() -> dict[str, str | bool | None]:
    stored = load_local_config(get_store_path())
    wizard = _as_mapping(stored.get("wizard"))
    targets = _as_mapping(stored.get("targets"))
    local = _as_mapping(targets.get("local"))
    raw_provider = local.get("provider")
    provider = PROVIDER_BY_VALUE.get(_string_value(raw_provider)) if raw_provider else None
    api_key_env = _string_value(local.get("api_key_env"), provider.api_key_env if provider else "")
    return {
        "wizard_mode": _string_value(wizard.get("mode"), "quickstart"),
        "provider": _string_value(raw_provider) if raw_provider else None,
        "model": _string_value(local.get("model")),
        "api_key_env": api_key_env,
        "has_api_key": bool(api_key_env and has_llm_api_key(api_key_env)),
        "legacy_api_key": _string_value(local.get("api_key")),
    }


def _integration_defaults(service: str) -> tuple[Mapping[str, object], Mapping[str, object]]:
    entry = _as_mapping(get_integration(service))
    return entry, _as_mapping(entry.get("credentials"))


def _step(title: str) -> None:
    _console.print(f"\n[bold]{title}[/]")


def _choice_title(choice: Choice) -> str:
    return choice.label


def _choice_description(choice: Choice) -> str | None:
    if choice.hint:
        return choice.hint
    return choice.group


def _questionary_choice(choice: Choice) -> questionary.Choice:
    return questionary.Choice(
        title=_choice_title(choice),
        value=choice.value,
        description=_choice_description(choice),
    )


def _choose(prompt: str, choices: list[Choice], *, default: str | None = None) -> str:
    q_choices = [_questionary_choice(choice) for choice in choices]

    result = select_prompt(
        prompt,
        choices=q_choices,
        default=default,
        style=_STYLE,
        instruction="(Use arrows to move, Enter to choose)",
    ).ask()

    if result is None:
        raise KeyboardInterrupt
    return str(result)


def _confirm(prompt: str, *, default: bool = True) -> bool:
    result = questionary.confirm(prompt, default=default, style=_STYLE).ask()
    if result is None:
        raise KeyboardInterrupt
    return bool(result)


def _prompt_value(
    label: str,
    *,
    default: str = "",
    secret: bool = False,
    allow_empty: bool = False,
) -> str:
    while True:
        instruction = "(Enter to keep current)" if default else None
        if secret:
            result = questionary.password(
                label,
                default=default,
                style=_STYLE,
                instruction=instruction,
            ).ask()
        else:
            result = questionary.text(
                label,
                default=default,
                style=_STYLE,
                instruction=instruction,
            ).ask()

        if result is None:
            raise KeyboardInterrupt

        value = str(result).strip()
        if value:
            return value
        if default:
            return default
        if allow_empty:
            return ""
        _console.print("[red]Required.[/]")


def _persist_llm_api_key(env_var: str, value: str) -> bool:
    try:
        save_llm_api_key(env_var, value)
    except RuntimeError as exc:
        _console.print(f"[red]{exc}[/]")
        return False
    return True


def _parse_csv_values(raw_value: str) -> list[str]:
    return [part.strip() for part in raw_value.split(",") if part.strip()]


def _display_probe(result: ProbeResult) -> None:
    status = "[green]reachable[/]" if result.reachable else "[red]unreachable[/]"
    _console.print(f"{result.target}: {status} [dim]({result.detail})[/]")


def _select_target_for_advanced(local_probe: ProbeResult, remote_probe: ProbeResult) -> str | None:
    _console.print("\n[dim]reachability[/]")
    _display_probe(local_probe)
    _display_probe(remote_probe)

    target = _choose(
        "Choose a configuration target:",
        [
            Choice(value="local", label="Local machine"),
            Choice(value="remote", label="Remote target (future support)"),
        ],
        default="local",
    )
    if target == "local":
        return "local"

    _console.print("\n[yellow]Remote setup is not available yet.[/]")
    if _confirm("Use local setup instead?", default=True):
        return "local"
    _console.print("[yellow]Setup cancelled.[/]")
    return None


def _render_header() -> None:
    _console.print()
    for line in _ASCII_HEADER.splitlines():
        _console.print(Text.assemble(("  ", ""), (line, "bold cyan")))
    _console.print()
    _console.print(
        Text.assemble(
            ("  ", ""),
            "open-source SRE agent for automated incident investigation and root cause analysis",
        )
    )
    _console.print()
    _console.print(Text.assemble(("  Setup", "bold white")))
    _console.print(
        Text.assemble(
            ("    ", ""), ("Configure your local AI stack and optional integrations.", "dim")
        )
    )
    _console.print()


def _render_saved_summary(
    *,
    provider_label: str,
    model: str,
    saved_path: str,
    env_path: str,
    configured_integrations: list[str],
) -> None:
    from app.integrations.store import STORE_PATH

    integrations = ", ".join(configured_integrations) or "none"
    _console.print("\n[green]Done.[/]")
    _console.print(f"[dim]provider      {provider_label}[/]")
    _console.print(f"[dim]model         {model}[/]")
    _console.print(f"[dim]services      {integrations}[/]")
    _console.print(f"[dim]config        {saved_path}[/]")
    _console.print(f"[dim]env           {env_path}[/]")
    _console.print("[dim]llm secret    system keychain[/]")
    _console.print(f"[dim]integrations  {STORE_PATH}[/]")


def _render_integration_result(service_label: str, result: IntegrationHealthResult) -> None:
    ok = bool(result.ok)
    detail = str(result.detail)
    color = "green" if ok else "red"
    prefix = "Connected" if ok else "Failed"
    _console.print(f"[{color}]{service_label} · {prefix}[/]")
    _console.print(f"[dim]{detail}[/]")


def _configure_grafana() -> tuple[str, str]:
    _, credentials = _integration_defaults("grafana")
    while True:
        endpoint = _prompt_value(
            "Grafana instance URL",
            default=_string_value(credentials.get("endpoint")),
        )
        api_key = _prompt_value(
            "Grafana service account token",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        with _console.status("Validating Grafana integration...", spinner="dots"):
            result = validate_grafana_integration(endpoint=endpoint, api_key=api_key)
        _render_integration_result("Grafana", result)
        if result.ok:
            upsert_integration(
                "grafana", {"credentials": {"endpoint": endpoint, "api_key": api_key}}
            )
            env_path = sync_env_values(
                {
                    "GRAFANA_INSTANCE_URL": endpoint,
                }
            )
            return "Grafana", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_grafana_local() -> tuple[str, str]:
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("docker"):
        _console.print("[red]Docker not found.[/]")
        _console.print("[dim]Install Docker Desktop and retry.[/]")
        return "Grafana Local (skipped)", ""

    # Check Docker daemon is actually running
    ping = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
    )
    if ping.returncode != 0:
        _console.print("[red]Docker is not running.[/]")
        _console.print("[dim]Start Docker Desktop, then run [bold]opensre onboard[/bold] again.[/]")
        return "Grafana Local (skipped)", ""

    compose_file = str(Path(__file__).parent / "local_grafana_stack/docker-compose.yml")
    with _console.status("Starting Grafana + Loki (docker compose up -d)...", spinner="dots"):
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file, "up", "-d"],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        _console.print("[red]Docker compose failed.[/]")
        _console.print(result.stderr or result.stdout)
        return "Grafana Local (skipped)", ""

    with _console.status("Waiting for Loki to be ready and seeding logs...", spinner="dots"):
        try:
            from app.cli.wizard.grafana_seed import seed_logs

            seed_logs()
        except (SystemExit, Exception) as exc:
            _console.print(f"[red]Loki seed failed: {exc}[/]")
            return "Grafana Local (skipped)", ""

    endpoint = "http://localhost:3000"
    api_key = ""
    remove_integration("grafana")  # clean up any stale grafana record pointing to localhost
    upsert_integration("grafana_local", {"credentials": {"endpoint": endpoint, "api_key": api_key}})
    env_path = sync_env_values({"GRAFANA_INSTANCE_URL": endpoint})
    _console.print("[green]Grafana Local · ready[/]")
    _console.print(f"[dim]UI: {endpoint}[/]")
    _console.print("[dim]Loki seeded with events_fact pipeline failure logs.[/]")
    _console.print("[dim]Run RCA:[/]")
    _console.print("[bold]  opensre investigate -i tests/fixtures/grafana_local_alert.json[/]")
    return "Grafana Local", str(env_path)


def _configure_datadog() -> tuple[str, str]:
    _, credentials = _integration_defaults("datadog")
    while True:
        api_key = _prompt_value(
            "Datadog API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        app_key = _prompt_value(
            "Datadog application key",
            default=_string_value(credentials.get("app_key")),
            secret=True,
        )
        site = _prompt_value(
            "Datadog site",
            default=_string_value(credentials.get("site"), "datadoghq.com"),
        )
        with _console.status("Validating Datadog integration...", spinner="dots"):
            result = validate_datadog_integration(api_key=api_key, app_key=app_key, site=site)
        _render_integration_result("Datadog", result)
        if result.ok:
            upsert_integration(
                "datadog",
                {"credentials": {"api_key": api_key, "app_key": app_key, "site": site}},
            )
            env_path = sync_env_values({})
            return "Datadog", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_honeycomb() -> tuple[str, str]:
    _, credentials = _integration_defaults("honeycomb")
    while True:
        api_key = _prompt_value(
            "Honeycomb configuration API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        dataset = _prompt_value(
            "Honeycomb dataset slug or __all__",
            default=_string_value(credentials.get("dataset"), "__all__"),
        )
        base_url = _prompt_value(
            "Honeycomb API URL",
            default=_string_value(credentials.get("base_url"), "https://api.honeycomb.io"),
        )
        with _console.status("Validating Honeycomb integration...", spinner="dots"):
            result = validate_honeycomb_integration(
                api_key=api_key,
                dataset=dataset,
                base_url=base_url,
            )
        _render_integration_result("Honeycomb", result)
        if result.ok:
            upsert_integration(
                "honeycomb",
                {"credentials": {"api_key": api_key, "dataset": dataset, "base_url": base_url}},
            )
            env_path = sync_env_values(
                {
                    "HONEYCOMB_DATASET": dataset,
                    "HONEYCOMB_API_URL": base_url,
                }
            )
            return "Honeycomb", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_coralogix() -> tuple[str, str]:
    _, credentials = _integration_defaults("coralogix")
    while True:
        api_key = _prompt_value(
            "Coralogix DataPrime API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        base_url = _prompt_value(
            "Coralogix API URL",
            default=_string_value(credentials.get("base_url"), "https://api.coralogix.com"),
        )
        application_name = _prompt_value(
            "Coralogix application name (optional)",
            default=_string_value(credentials.get("application_name")),
            allow_empty=True,
        )
        subsystem_name = _prompt_value(
            "Coralogix subsystem name (optional)",
            default=_string_value(credentials.get("subsystem_name")),
            allow_empty=True,
        )
        with _console.status("Validating Coralogix integration...", spinner="dots"):
            result = validate_coralogix_integration(
                api_key=api_key,
                base_url=base_url,
                application_name=application_name,
                subsystem_name=subsystem_name,
            )
        _render_integration_result("Coralogix", result)
        if result.ok:
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
            env_path = sync_env_values(
                {
                    "CORALOGIX_API_URL": base_url,
                    "CORALOGIX_APPLICATION_NAME": application_name,
                    "CORALOGIX_SUBSYSTEM_NAME": subsystem_name,
                }
            )
            return "Coralogix", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_slack() -> tuple[str, str]:
    _, credentials = _integration_defaults("slack")
    while True:
        webhook_url = _prompt_value(
            "Slack webhook URL",
            default=_string_value(credentials.get("webhook_url")),
            secret=True,
        )
        with _console.status("Validating Slack webhook...", spinner="dots"):
            result = validate_slack_webhook(webhook_url=webhook_url)
        _render_integration_result("Slack", result)
        if result.ok:
            env_path = sync_env_values({})
            return "Slack", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_aws() -> tuple[str, str]:
    existing, credentials = _integration_defaults("aws")
    default_auth_mode = "role" if _string_value(existing.get("role_arn")) else "keys"
    auth_mode = _choose(
        "Choose the AWS authentication method:",
        [
            Choice(value="role", label="IAM role ARN"),
            Choice(value="keys", label="Access key + secret"),
        ],
        default=default_auth_mode,
    )

    while True:
        region = _prompt_value(
            "AWS region",
            default=_string_value(credentials.get("region"), "us-east-1"),
        )
        if auth_mode == "role":
            role_arn = _prompt_value(
                "IAM role ARN",
                default=_string_value(existing.get("role_arn")),
            )
            external_id = _prompt_value(
                "External ID",
                default=_string_value(existing.get("external_id")),
                allow_empty=True,
            )
            with _console.status("Validating AWS role...", spinner="dots"):
                result = validate_aws_integration(
                    region=region,
                    role_arn=role_arn,
                    external_id=external_id,
                )
            _render_integration_result("AWS", result)
            if result.ok:
                upsert_integration(
                    "aws",
                    {
                        "role_arn": role_arn,
                        "external_id": external_id,
                        "credentials": {"region": region},
                    },
                )
                env_path = sync_env_values({"AWS_REGION": region})
                return "AWS", str(env_path)
        else:
            access_key_id = _prompt_value(
                "AWS access key ID",
                default=_string_value(credentials.get("access_key_id")),
                secret=True,
            )
            secret_access_key = _prompt_value(
                "AWS secret access key",
                default=_string_value(credentials.get("secret_access_key")),
                secret=True,
            )
            session_token = _prompt_value(
                "AWS session token",
                default=_string_value(credentials.get("session_token")),
                secret=True,
                allow_empty=True,
            )
            with _console.status("Validating AWS credentials...", spinner="dots"):
                result = validate_aws_integration(
                    region=region,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    session_token=session_token,
                )
            _render_integration_result("AWS", result)
            if result.ok:
                upsert_integration(
                    "aws",
                    {
                        "credentials": {
                            "access_key_id": access_key_id,
                            "secret_access_key": secret_access_key,
                            "session_token": session_token,
                            "region": region,
                        }
                    },
                )
                env_path = sync_env_values(
                    {
                        "AWS_REGION": region,
                    }
                )
                return "AWS", str(env_path)

        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_github_mcp() -> tuple[str, str]:
    _, credentials = _integration_defaults("github")
    default_mode = _string_value(credentials.get("mode"), DEFAULT_GITHUB_MCP_MODE)
    mode = _choose(
        "Choose the GitHub MCP transport:",
        [
            Choice(value="sse", label="SSE"),
            Choice(value="streamable-http", label="Streamable HTTP"),
            Choice(value="stdio", label="stdio"),
        ],
        default=default_mode,
    )

    while True:
        url = ""
        command = ""
        args: list[str] = []
        if mode == "stdio":
            command = _prompt_value(
                "GitHub MCP command",
                default=_string_value(credentials.get("command"), "github-mcp-server"),
            )
            args_raw = _prompt_value(
                "GitHub MCP args",
                default=_joined_values(
                    credentials.get("args"),
                    separator=" ",
                    fallback="stdio --toolsets repos,issues,pull_requests,actions",
                ),
            )
            args = [part for part in args_raw.split() if part]
        else:
            url = _prompt_value(
                "GitHub MCP URL",
                default=_string_value(credentials.get("url"), DEFAULT_GITHUB_MCP_URL),
            )

        toolsets = _parse_csv_values(
            _prompt_value(
                "GitHub MCP toolsets (comma-separated)",
                default=_joined_values(
                    credentials.get("toolsets"),
                    separator=",",
                    fallback="repos,issues,pull_requests,actions",
                ),
            )
        )
        auth_token = _prompt_value(
            "GitHub PAT / auth token (optional if the server already authenticates upstream)",
            default=_string_value(credentials.get("auth_token")),
            secret=True,
            allow_empty=True,
        )

        with _console.status("Validating GitHub MCP integration...", spinner="dots"):
            result = validate_github_mcp_integration(
                url=url,
                mode=mode,
                auth_token=auth_token,
                command=command,
                args=args,
                toolsets=toolsets,
            )
        _render_integration_result("GitHub MCP", result)
        if result.ok:
            credentials = {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args,
                "toolsets": toolsets,
            }
            upsert_integration("github", {"credentials": credentials})
            env_path = sync_env_values(
                {
                    "GITHUB_MCP_URL": url,
                    "GITHUB_MCP_MODE": mode,
                    "GITHUB_MCP_COMMAND": command,
                    "GITHUB_MCP_ARGS": " ".join(args),
                    "GITHUB_MCP_TOOLSETS": ",".join(toolsets),
                }
            )
            return "GitHub MCP", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_openclaw() -> tuple[str, str]:
    _, credentials = _integration_defaults("openclaw")
    stored_command = _string_value(credentials.get("command"))
    stored_args = credentials.get("args")
    use_stdio_defaults = _looks_like_openclaw_control_ui_url(credentials.get("url")) or (
        stored_command == "openclaw-mcp" and not _joined_values(stored_args, separator=" ", fallback="")
    )
    default_mode = (
        DEFAULT_OPENCLAW_MCP_MODE
        if use_stdio_defaults
        else _string_value(credentials.get("mode"), DEFAULT_OPENCLAW_MCP_MODE)
    )

    while True:
        mode = _choose(
            "Choose the OpenClaw MCP transport:",
            [
                Choice(value="stdio", label="stdio (recommended)"),
                Choice(value="streamable-http", label="Streamable HTTP"),
                Choice(value="sse", label="SSE"),
            ],
            default=default_mode,
        )

        url = ""
        command = ""
        args: list[str] = []
        auth_token = ""
        if mode == "stdio":
            command = _prompt_value(
                "OpenClaw MCP command",
                default=(
                    DEFAULT_OPENCLAW_MCP_COMMAND
                    if use_stdio_defaults
                    else _string_value(credentials.get("command"), DEFAULT_OPENCLAW_MCP_COMMAND)
                ),
            )
            args_raw = _prompt_value(
                "OpenClaw MCP args",
                default=(
                    " ".join(DEFAULT_OPENCLAW_MCP_ARGS)
                    if use_stdio_defaults
                    else _joined_values(
                        credentials.get("args"),
                        separator=" ",
                        fallback=" ".join(DEFAULT_OPENCLAW_MCP_ARGS),
                    )
                ),
                allow_empty=True,
            )
            args = [part for part in args_raw.split() if part]
        else:
            url = _prompt_value(
                "OpenClaw MCP URL",
                default=_string_value(credentials.get("url"), DEFAULT_OPENCLAW_MCP_URL),
            )
            auth_token = _prompt_value(
                "OpenClaw auth token (optional)",
                default=_string_value(credentials.get("auth_token")),
                secret=True,
                allow_empty=True,
            )

        credentials = {
            **credentials,
            "url": url,
            "mode": mode,
            "auth_token": auth_token,
            "command": command,
            "args": args,
        }

        with _console.status("Validating OpenClaw MCP integration...", spinner="dots"):
            result = validate_openclaw_integration(
                url=url,
                mode=mode,
                auth_token=auth_token,
                command=command,
                args=args,
            )
        _render_integration_result("OpenClaw", result)
        if result.ok:
            credentials_dict = {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args,
            }
            upsert_integration("openclaw", {"credentials": credentials_dict})
            env_path = sync_env_values(
                {
                    "OPENCLAW_MCP_URL": url,
                    "OPENCLAW_MCP_MODE": mode,
                    "OPENCLAW_MCP_AUTH_TOKEN": auth_token,
                    "OPENCLAW_MCP_COMMAND": command,
                    "OPENCLAW_MCP_ARGS": " ".join(args),
                }
            )
            return "OpenClaw", str(env_path)
        default_mode = mode
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_gitlab() -> tuple[str, str]:
    _, credentials = _integration_defaults("gitlab")

    while True:
        base_url = _prompt_value(
            "Gitlab base URL",
            default=_string_value(credentials.get("base_url"), DEFAULT_GITLAB_BASE_URL),
        )
        auth_token = _prompt_value(
            "Gitlab access token",
            default=_string_value(credentials.get("auth_token")),
            secret=True,
        )

        with _console.status("Validating Gitlab integration...", spinner="dots"):
            result = validate_gitlab_integration(base_url=base_url, auth_token=auth_token)
        _render_integration_result("Gitlab", result)
        if result.ok:
            credentials = {"base_url": base_url, "auth_token": auth_token}
            upsert_integration("gitlab", {"credentials": credentials})
            env_path = sync_env_values({"GITLAB_BASE_URL": base_url})
            return "Gitlab", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_sentry() -> tuple[str, str]:
    _, credentials = _integration_defaults("sentry")
    guidance = get_sentry_auth_recommendations()
    _console.print(
        "[dim]Recommended: "
        f"{guidance['recommended_token_type']} from {guidance['where_to_create']}. "
        f"{guidance['fallback_token_type']} only if you need broader scopes.[/]"
    )

    while True:
        base_url = _prompt_value(
            "Sentry base URL",
            default=_string_value(credentials.get("base_url"), DEFAULT_SENTRY_URL),
        )
        organization_slug = _prompt_value(
            "Sentry organization slug",
            default=_string_value(credentials.get("organization_slug")),
        )
        project_slug = _prompt_value(
            "Sentry project slug (optional)",
            default=_string_value(credentials.get("project_slug")),
            allow_empty=True,
        )
        auth_token = _prompt_value(
            "Sentry auth token",
            default=_string_value(credentials.get("auth_token")),
            secret=True,
        )

        with _console.status("Validating Sentry integration...", spinner="dots"):
            result = validate_sentry_integration(
                base_url=base_url,
                organization_slug=organization_slug,
                auth_token=auth_token,
                project_slug=project_slug,
            )
        _render_integration_result("Sentry", result)
        if result.ok:
            credentials = {
                "base_url": base_url,
                "organization_slug": organization_slug,
                "auth_token": auth_token,
                "project_slug": project_slug,
            }
            upsert_integration("sentry", {"credentials": credentials})
            env_path = sync_env_values(
                {
                    "SENTRY_URL": base_url,
                    "SENTRY_ORG_SLUG": organization_slug,
                    "SENTRY_PROJECT_SLUG": project_slug,
                }
            )
            return "Sentry", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")

def _configure_notion() -> tuple[str, str]:
    _, credentials = _integration_defaults("notion")
    _console.print("\n[bold]Notion Integration[/bold]")
    _console.print("Create an internal integration at https://www.notion.so/my-integrations")
    _console.print("then share your target database with the integration.\n")

    while True:
        api_key = _prompt_value("Notion API key (secret_...)", secret=True)
        database_id = _prompt_value("Notion database ID")

        with _console.status("Validating Notion connection...", spinner="dots"):
            result = validate_notion_integration(api_key=api_key, database_id=database_id)
        _render_integration_result("Notion", result)

        if result.ok:
            upsert_integration("notion", {"credentials": {"api_key": api_key, "database_id": database_id}})
            env_path = sync_env_values({"NOTION_DATABASE_ID": database_id})
            return "Notion", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")

def _configure_jira() -> tuple[str, str]:
    _, credentials = _integration_defaults("jira")
    _console.print("\n[bold]Jira Integration[/bold]")
    _console.print(
        "Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens\n"
    )

    while True:
        base_url = _prompt_value("Jira base URL (e.g. https://myteam.atlassian.net)")
        email = _prompt_value("Jira account email")
        api_token = _prompt_value("Jira API token", secret=True)
        project_key = _prompt_value("Jira project key (e.g. OPS)")

        with _console.status("Validating Jira connection...", spinner="dots"):
            result = validate_jira_integration(
                base_url=base_url,
                email=email,
                api_token=api_token,
                project_key=project_key,
            )
        _render_integration_result("Jira", result)

        if result.ok:
            upsert_integration(
                "jira",
                {
                    "credentials": {
                        "base_url": base_url,
                        "email": email,
                        "api_token": api_token,
                        "project_key": project_key,
                    }
                },
            )
            env_path = sync_env_values({})
            return "Jira", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_google_docs() -> tuple[str, str]:
    _, credentials = _integration_defaults("google_docs")
    while True:
        credentials_file = _prompt_value(
            "Path to Google service account credentials JSON file",
            default=_string_value(credentials.get("credentials_file")),
        )
        folder_id = _prompt_value(
            "Google Drive folder ID for incident reports",
            default=_string_value(credentials.get("folder_id")),
        )
        with _console.status("Validating Google Docs integration...", spinner="dots"):
            result = validate_google_docs_integration(
                credentials_file=credentials_file,
                folder_id=folder_id,
            )
        _render_integration_result("Google Docs", result)
        if result.ok:
            upsert_integration(
                "google_docs",
                {
                    "credentials": {
                        "credentials_file": credentials_file,
                        "folder_id": folder_id,
                    }
                },
            )
            env_path = sync_env_values(
                {
                    "GOOGLE_CREDENTIALS_FILE": credentials_file,
                    "GOOGLE_DRIVE_FOLDER_ID": folder_id,
                }
            )
            return "Google Docs", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_vercel() -> tuple[str, str]:
    _, credentials = _integration_defaults("vercel")
    while True:
        api_token = _prompt_value(
            "Vercel API token (Account Settings > Tokens)",
            default=_string_value(credentials.get("api_token")),
            secret=True,
        )
        team_id = _prompt_value(
            "Vercel team ID (optional, for team-scoped access)",
            default=_string_value(credentials.get("team_id")),
            allow_empty=True,
        )
        with _console.status("Validating Vercel integration...", spinner="dots"):
            result = validate_vercel_integration(api_token=api_token, team_id=team_id)
        _render_integration_result("Vercel", result)
        if result.ok:
            upsert_integration(
                "vercel",
                {"credentials": {"api_token": api_token, "team_id": team_id}},
            )
            env_path = sync_env_values({})
            return "Vercel", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_opsgenie() -> tuple[str, str]:
    _, credentials = _integration_defaults("opsgenie")
    while True:
        api_key = _prompt_value(
            "OpsGenie API key (Settings > API key management)",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        region = _prompt_value(
            "OpsGenie region (us or eu)",
            default=_string_value(credentials.get("region"), "us"),
        )
        with _console.status("Validating OpsGenie integration...", spinner="dots"):
            result = validate_opsgenie_integration(api_key=api_key, region=region)
        _render_integration_result("OpsGenie", result)
        if result.ok:
            upsert_integration(
                "opsgenie",
                {"credentials": {"api_key": api_key, "region": region}},
            )
            env_path = sync_env_values({})
            return "OpsGenie", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_discord() -> tuple[str, str]:
    _, credentials = _integration_defaults("discord")
    _console.print(
        "\n[bold]Discord Integration[/bold]\n"
        "[dim]Get your credentials from https://discord.com/developers/applications.[/]\n"
    )
    while True:
        bot_token = _prompt_value(
            "Discord bot token",
            default=_string_value(credentials.get("bot_token")),
            secret=True,
        )
        application_id = _prompt_value(
            "Discord application ID",
            default=_string_value(credentials.get("application_id")),
        )
        public_key = _prompt_value(
            "Discord public key (from Developer Portal)",
            default=_string_value(credentials.get("public_key")),
        )
        default_channel_id = _prompt_value(
            "Default channel ID (optional)",
            default=_string_value(credentials.get("default_channel_id")),
            allow_empty=True,
        )
        with _console.status("Validating Discord bot token...", spinner="dots"):
            result = validate_discord_bot(bot_token=bot_token)
        _render_integration_result("Discord", result)
        if result.ok:
            upsert_integration(
                "discord",
                {
                    "credentials": {
                        "bot_token": bot_token,
                        "application_id": application_id,
                        "public_key": public_key,
                        "default_channel_id": default_channel_id,
                    }
                },
            )
            from app.integrations.cli import _register_discord_slash_command

            _register_discord_slash_command(application_id, bot_token)
            env_path = sync_env_values({
                "DISCORD_BOT_TOKEN": bot_token,
                "DISCORD_APPLICATION_ID": application_id,
                "DISCORD_PUBLIC_KEY": public_key,
                "DISCORD_DEFAULT_CHANNEL_ID": default_channel_id,
            })
            return "Discord", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_selected_integrations() -> tuple[list[str], str | None]:
    configured: list[str] = []
    last_env_path: str | None = None

    _console.print(
        "[dim]Pick one integration to wire up now, or skip this step and come back later.[/]"
    )
    integration_choices = [
        Choice(
            value="grafana_local",
            label="Grafana Local (Docker)",
            hint="Starts Grafana + Loki and seeds demo alerts",
        ),
        Choice(
            value="grafana",
            label="Grafana Cloud / self-hosted",
            hint="Connect an existing Grafana instance",
        ),
        Choice(value="datadog", label="Datadog", hint="Logs, monitors, and Kubernetes context"),
        Choice(value="honeycomb", label="Honeycomb", hint="Query traces and spans from Honeycomb"),
        Choice(value="coralogix", label="Coralogix", hint="Query logs from Coralogix DataPrime"),
        Choice(value="slack", label="Slack", hint="Send findings to a webhook or channel"),
        Choice(value="discord", label="Discord", hint="Trigger investigations via slash commands and post findings to threads"),
        Choice(value="aws", label="AWS", hint="Inspect CloudWatch, EKS, and account resources"),
        Choice(
            value="github", label="GitHub MCP", hint="Let the agent inspect repos, PRs, and issues"
        ),
        Choice(
            value="sentry", label="Sentry", hint="Investigate errors, events, and issue history"
        ),
        Choice(value="gitlab", label="Gitlab", hint="Let the agent inspect repos, PRs, and issues"),
        Choice(
            value="google_docs",
            label="Google Docs",
            hint="Create shareable incident postmortem reports",
        ),
        Choice(
            value="vercel",
            label="Vercel",
            hint=(
                "Deployments, build output, and logs tools; runtime-log API can lag the dashboard"
            ),
        ),
        Choice(
            value="jira",
            label="Jira",
            hint="File and update incident tickets automatically",
        ),
        Choice(
            value="opsgenie",
            label="OpsGenie",
            hint="Investigate alerts and triage state from OpsGenie",
        ),
        Choice(
            value="notion",
            label="Notion",
            hint="Post investigation reports to a Notion database",
        ),
        Choice(
            value="openclaw",
            label="OpenClaw",
            hint="Connect OpenSRE to OpenClaw so your AI coding assistant can trigger investigations",
        ),
        Choice(
            value="skip",
            label="Skip for now",
            hint="Finish onboarding without configuring an integration",
        ),
    ]
    selected_service = _choose(
        "Choose an integration to configure",
        integration_choices,
        default="grafana_local",
    )
    if selected_service == "skip":
        return configured, last_env_path

    handlers = {
        "grafana_local": _configure_grafana_local,
        "grafana": _configure_grafana,
        "datadog": _configure_datadog,
        "honeycomb": _configure_honeycomb,
        "coralogix": _configure_coralogix,
        "slack": _configure_slack,
        "discord": _configure_discord,
        "aws": _configure_aws,
        "github": _configure_github_mcp,
        "sentry": _configure_sentry,
        "gitlab": _configure_gitlab,
        "google_docs": _configure_google_docs,
        "vercel": _configure_vercel,
        "jira": _configure_jira,
        "opsgenie": _configure_opsgenie,
        "notion": _configure_notion,
        "openclaw": _configure_openclaw,
    }
    _SERVICE_LABELS = {
        "grafana_local": "grafana local",
        "grafana": "grafana",
        "datadog": "datadog",
        "honeycomb": "honeycomb",
        "coralogix": "coralogix",
        "slack": "slack",
        "discord": "discord",
        "aws": "aws",
        "github": "github mcp",
        "sentry": "sentry",
        "gitlab": "gitlab",
        "google_docs": "google docs",
        "vercel": "vercel",
        "jira": "jira",
        "opsgenie": "opsgenie",
        "notion": "notion",
        "openclaw": "openclaw",
    }

    _step(f"Service · {_SERVICE_LABELS.get(selected_service, selected_service)}")
    if selected_service == "vercel":
        _console.print(
            "[dim]Note: Vercel's runtime-log API may omit or delay lines compared to the "
            "dashboard. Deployment and build checks still apply; there is no CLI incident browser.[/]"
        )
    try:
        label, env_path = handlers[selected_service]()
        configured.append(label)
        last_env_path = env_path
    except KeyboardInterrupt:
        _console.print(
            f"[yellow]{_SERVICE_LABELS.get(selected_service, selected_service)} setup skipped.[/]"
        )

    return configured, last_env_path


def _render_demo_response(demo_response: dict) -> None:
    topics = ", ".join(demo_response.get("topics", [])) or "none"
    guidance = demo_response.get("guidance") or []
    summary = [
        f"demo      {'ready' if demo_response.get('success') else 'failed'}",
        f"topics    {topics}",
    ]
    if guidance:
        first = guidance[0]
        summary.append(f"sample    {first.get('topic', 'unknown')}")
        content = str(first.get("content", "")).strip().splitlines()
        if content:
            summary.append(f"preview   {content[0][:140]}")
    _console.print("\n[bold]summary[/]")
    for line in summary:
        _console.print(f"[dim]{line}[/]")


def _render_next_steps() -> None:
    _console.print("\n[bold]next[/]")
    _console.print("[dim]opensre onboard[/]")
    _console.print(
        "[dim]opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json[/]"
    )


def run_wizard(_argv: list[str] | None = None) -> int:
    """Run the interactive wizard."""
    _render_header()
    defaults = _local_defaults()
    saved_provider_value = defaults["provider"] if isinstance(defaults["provider"], str) else None
    saved_model_value = defaults["model"] if isinstance(defaults["model"], str) else ""
    default_wizard_mode = (
        defaults["wizard_mode"] if isinstance(defaults["wizard_mode"], str) else "quickstart"
    )
    default_provider_value = (
        saved_provider_value
        if saved_provider_value in PROVIDER_BY_VALUE
        else SUPPORTED_PROVIDERS[0].value
    )

    _step("Setup Mode")
    wizard_mode = _choose(
        "How do you want to get started?",
        [
            Choice(
                value="quickstart", label="Quickstart", hint="Local setup with the usual defaults"
            ),
            Choice(
                value="advanced",
                label="Advanced",
                hint="Show probes and choose the target explicitly",
            ),
        ],
        default=default_wizard_mode,
    )

    store_path = get_store_path()
    local_probe = probe_local_target(store_path)
    remote_probe = ProbeResult(
        target="remote",
        reachable=False,
        detail="Remote probing is shown during Advanced setup.",
    )

    if wizard_mode == "advanced":
        remote_probe = probe_remote_target()
        target = _select_target_for_advanced(local_probe, remote_probe)
        if target is None:
            return 1
    else:
        target = "local"

    if target != "local":
        print("Only local configuration is supported today.", file=sys.stderr)
        return 1

    _step("LLM Provider")
    saved_provider = PROVIDER_BY_VALUE.get(saved_provider_value) if saved_provider_value else None
    if saved_provider is not None:
        current_model = saved_model_value or saved_provider.default_model
        _console.print(f"[dim]current provider  {saved_provider.label}  ·  {current_model}[/]")
        change_provider = _confirm("Change provider?", default=False)
    else:
        change_provider = True

    if change_provider:
        provider = PROVIDER_BY_VALUE[
            _choose(
                "Choose your LLM provider",
                [
                    Choice(
                        value=p.value,
                        label=p.label,
                        hint=p.group,
                    )
                    for p in SUPPORTED_PROVIDERS
                ],
                default=default_provider_value,
            )
        ]
        model = provider.default_model
        _step(provider.credential_label.title())
        try:
            api_key = _prompt_value(
                f"{provider.label} {provider.credential_label} ({provider.api_key_env})",
                default=provider.credential_default,
                secret=provider.credential_secret,
            )
        except KeyboardInterrupt:
            _console.print("\n[yellow]Setup cancelled.[/]")
            return 1
        if not _persist_llm_api_key(provider.api_key_env, api_key):
            return 1
    else:
        assert saved_provider is not None
        provider = saved_provider
        model = saved_model_value or provider.default_model
        has_api_key = bool(defaults["has_api_key"])
        legacy_api_key = str(defaults["legacy_api_key"] or "").strip()
        if not has_api_key and legacy_api_key:
            if not _persist_llm_api_key(provider.api_key_env, legacy_api_key):
                return 1
            has_api_key = True
        if not has_api_key:
            _step(provider.credential_label.title())
            try:
                api_key = _prompt_value(
                    f"{provider.label} {provider.credential_label} ({provider.api_key_env})",
                    default=provider.credential_default,
                    secret=provider.credential_secret,
                )
            except KeyboardInterrupt:
                _console.print("\n[yellow]Setup cancelled.[/]")
                return 1
            if not _persist_llm_api_key(provider.api_key_env, api_key):
                return 1

    probes = {
        "local": local_probe.as_dict(),
        "remote": remote_probe.as_dict(),
    }
    saved_path = save_local_config(
        wizard_mode=wizard_mode,
        provider=provider.value,
        model=model,
        api_key_env=provider.api_key_env,
        model_env=provider.model_env,
        probes=probes,
    )
    env_path = sync_provider_env(provider=provider, model=model)

    _step("Integrations")
    try:
        configured_integrations, integration_env_path = _configure_selected_integrations()
    except KeyboardInterrupt:
        _console.print("\n[yellow]Integration setup cancelled. AI config was kept.[/]")
        configured_integrations = []
        integration_env_path = None

    summary_env_path = integration_env_path or str(env_path)

    _render_saved_summary(
        provider_label=provider.label,
        model=model,
        saved_path=str(saved_path),
        env_path=summary_env_path,
        configured_integrations=configured_integrations,
    )
    demo_response = build_demo_action_response()
    _render_demo_response(demo_response)
    _render_next_steps()
    return 0
