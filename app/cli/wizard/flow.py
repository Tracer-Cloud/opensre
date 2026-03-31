"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass

import questionary
from questionary import Choice as QuestionaryChoice
from questionary import Style
from rich.console import Console

from app.cli.wizard.config import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS, ProviderOption
from app.cli.wizard.env_sync import sync_env_values, sync_provider_env
from app.cli.wizard.probes import ProbeResult, probe_local_target, probe_remote_target
from app.cli.wizard.prompts import checkbox as checkbox_prompt
from app.cli.wizard.prompts import select as select_prompt
from app.cli.wizard.store import get_store_path, load_local_config, save_local_config
from app.integrations.store import get_integration, remove_integration, upsert_integration

_console = Console()
DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
DEFAULT_GITHUB_MCP_MODE = "streamable-http"
DEFAULT_SENTRY_URL = "https://sentry.io"


def validate_provider_credentials(**kwargs):
    from app.cli.wizard.validation import validate_provider_credentials as _validate

    return _validate(**kwargs)


def build_demo_action_response():
    from app.cli.wizard.validation import build_demo_action_response as _build

    return _build()


def validate_grafana_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_grafana_integration as _validate

    return _validate(**kwargs)


def validate_datadog_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_datadog_integration as _validate

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


def validate_sentry_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_sentry_integration as _validate

    return _validate(**kwargs)


def get_sentry_auth_recommendations():
    from app.integrations.sentry import get_sentry_auth_recommendations as _get

    return _get()


@dataclass(frozen=True)
class IntegrationHealthResult:
    ok: bool
    detail: str

_STYLE = Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("answer", "fg:cyan bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:green"),
        ("separator", "fg:cyan"),
        ("instruction", "fg:#858585 italic"),
    ]
)


@dataclass(frozen=True)
class Choice:
    """A selectable wizard choice."""

    value: str
    label: str
    group: str | None = None


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


def _local_defaults() -> dict[str, str]:
    stored = load_local_config(get_store_path())
    wizard = _as_mapping(stored.get("wizard"))
    targets = _as_mapping(stored.get("targets"))
    local = _as_mapping(targets.get("local"))
    return {
        "wizard_mode": _string_value(wizard.get("mode"), "quickstart"),
        "provider": _string_value(local.get("provider"), SUPPORTED_PROVIDERS[0].value),
        "model": _string_value(local.get("model")),
        "api_key": _string_value(local.get("api_key")),
        "api_key_env": _string_value(local.get("api_key_env")),
    }


def _integration_defaults(service: str) -> tuple[Mapping[str, object], Mapping[str, object]]:
    entry = _as_mapping(get_integration(service))
    return entry, _as_mapping(entry.get("credentials"))


def _step(title: str) -> None:
    _console.print(f"\n[bold]{title.lower()}[/]")


def _choose(prompt: str, choices: list[Choice], *, default: str | None = None) -> str:
    q_choices = []
    for choice in choices:
        suffix = f" ({choice.group})" if choice.group else ""
        label = f"{choice.label}{suffix}"
        q_choices.append(QuestionaryChoice(title=label, value=choice.value))

    result = select_prompt(
        prompt,
        choices=q_choices,
        default=default,
        style=_STYLE,
        instruction="(Tab, arrows, Enter)",
    ).ask()

    if result is None:
        raise KeyboardInterrupt
    return str(result)


def _choose_many(prompt: str, choices: list[Choice], *, default: list[str] | None = None) -> list[str]:
    q_choices = [QuestionaryChoice(title=choice.label, value=choice.value) for choice in choices]

    result = checkbox_prompt(
        prompt,
        choices=q_choices,
        style=_STYLE,
        instruction="(Space to select, Enter to confirm)",
        default=default or [],
    ).ask()

    if result is None:
        raise KeyboardInterrupt
    return list(result)


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


def _parse_csv_values(raw_value: str) -> list[str]:
    return [part.strip() for part in raw_value.split(",") if part.strip()]


def _collect_validated_api_key(
    provider: ProviderOption,
    model: str,
    *,
    default_api_key: str = "",
    auto_use_saved_key: bool = False,
) -> str:
    if auto_use_saved_key and default_api_key:
        with _console.status(f"Validating {provider.label} API key...", spinner="dots"):
            result = validate_provider_credentials(
                provider=provider,
                api_key=default_api_key,
                model=model,
            )
        if result.ok:
            _console.print(f"[green]Using saved {provider.label} key.[/]")
            _console.print(f"[dim]{result.detail}[/]")
            if result.sample_response:
                _console.print(f"[dim]Sample: {result.sample_response}[/]")
            return default_api_key
        _console.print(f"[yellow]Saved {provider.label} key failed validation.[/]")
        _console.print(f"[dim]{result.detail}[/]")
        default_api_key = ""

    while True:
        api_key = _prompt_value(
            f"{provider.label} API key ({provider.api_key_env})",
            default=default_api_key,
            secret=True,
        )
        with _console.status(f"Validating {provider.label} API key...", spinner="dots"):
            result = validate_provider_credentials(provider=provider, api_key=api_key, model=model)
        if result.ok:
            _console.print("[green]Connected.[/]")
            if result.sample_response:
                _console.print(f"[dim]{result.detail}[/]")
                _console.print(f"[dim]Sample: {result.sample_response}[/]")
            else:
                _console.print(f"[dim]{result.detail}[/]")
            return api_key
        _console.print(f"[red]Validation failed: {result.detail}[/]")
        _console.print("[dim]Enter retries the current key. Paste a new one to replace it.[/]")
        default_api_key = api_key


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
    _console.print("[bold]OpenSRE[/]")
    _console.print("[dim]Set up local AI and integrations.[/]")


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
            upsert_integration("grafana", {"credentials": {"endpoint": endpoint, "api_key": api_key}})
            env_path = sync_env_values(
                {
                    "GRAFANA_INSTANCE_URL": endpoint,
                    "GRAFANA_READ_TOKEN": api_key,
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

    compose_file = str(Path(__file__).parents[3] / "app/demo/local_grafana_stack/docker-compose.yml")
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
            from app.demo.local_grafana_seed import seed_logs
            seed_logs()
        except (SystemExit, Exception) as exc:
            _console.print(f"[red]Loki seed failed: {exc}[/]")
            return "Grafana Local (skipped)", ""

    endpoint = "http://localhost:3000"
    api_key = ""
    remove_integration("grafana")  # clean up any stale grafana record pointing to localhost
    upsert_integration("grafana_local", {"credentials": {"endpoint": endpoint, "api_key": api_key}})
    env_path = sync_env_values({"GRAFANA_INSTANCE_URL": endpoint, "GRAFANA_READ_TOKEN": api_key})
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
            env_path = sync_env_values(
                {
                    "DD_API_KEY": api_key,
                    "DD_APP_KEY": app_key,
                }
            )
            return "Datadog", str(env_path)
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
            env_path = sync_env_values({"SLACK_WEBHOOK_URL": webhook_url})
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
                        "AWS_ACCESS_KEY_ID": access_key_id,
                        "AWS_SECRET_ACCESS_KEY": secret_access_key,
                        "AWS_SESSION_TOKEN": session_token,
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
            env_path = sync_env_values({
                "GITHUB_MCP_URL": url,
                "GITHUB_MCP_MODE": mode,
                "GITHUB_MCP_COMMAND": command,
                "GITHUB_MCP_ARGS": " ".join(args),
                "GITHUB_MCP_AUTH_TOKEN": auth_token,
                "GITHUB_MCP_TOOLSETS": ",".join(toolsets),
            })
            return "GitHub MCP", str(env_path)
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
            env_path = sync_env_values({
                "SENTRY_URL": base_url,
                "SENTRY_ORG_SLUG": organization_slug,
                "SENTRY_PROJECT_SLUG": project_slug,
                "SENTRY_AUTH_TOKEN": auth_token,
            })
            return "Sentry", str(env_path)
        _console.print("[dim]Try again or press Ctrl+C to cancel.[/]")


def _configure_selected_integrations() -> tuple[list[str], str | None]:
    configured: list[str] = []
    last_env_path: str | None = None

    _console.print("[dim]Use Space to select, Enter to confirm.[/]")
    selected = _choose_many(
        "Integrations (optional):",
        [
            Choice(value="grafana_local", label="Grafana Local (Docker) — starts Grafana + Loki, seeds test data"),
            Choice(value="grafana", label="Grafana Cloud / self-hosted"),
            Choice(value="datadog", label="Datadog"),
            Choice(value="slack", label="Slack"),
            Choice(value="aws", label="AWS"),
            Choice(value="github", label="GitHub MCP"),
            Choice(value="sentry", label="Sentry"),
        ],
        default=["grafana_local"],
    )

    handlers = {
        "grafana_local": _configure_grafana_local,
        "grafana": _configure_grafana,
        "datadog": _configure_datadog,
        "slack": _configure_slack,
        "aws": _configure_aws,
        "github": _configure_github_mcp,
        "sentry": _configure_sentry,
    }
    _SERVICE_LABELS = {
        "grafana_local": "grafana local",
        "grafana": "grafana",
        "datadog": "datadog",
        "slack": "slack",
        "aws": "aws",
        "github": "github mcp",
        "sentry": "sentry",
    }
    for index, service in enumerate(selected, start=1):
        _step(f"service {index}/{len(selected)} · {_SERVICE_LABELS.get(service, service)}")
        try:
            label, env_path = handlers[service]()
            configured.append(label)
            last_env_path = env_path
        except KeyboardInterrupt:
            _console.print(f"[yellow]{_SERVICE_LABELS.get(service, service)} setup skipped.[/]")

    return configured, last_env_path


def _render_demo_response(demo_response: dict) -> None:
    topics = ", ".join(demo_response.get("topics", [])) or "none"
    guidance = demo_response.get("guidance") or []
    summary = [f"demo      {'ready' if demo_response.get('success') else 'failed'}", f"topics    {topics}"]
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
    _console.print("[dim]opensre investigate -i tests/fixtures/grafana_local_alert.json[/]")


def run_wizard(_argv: list[str] | None = None) -> int:
    """Run the interactive wizard."""
    _render_header()
    defaults = _local_defaults()
    default_provider_value = defaults["provider"]
    if default_provider_value not in PROVIDER_BY_VALUE:
        default_provider_value = SUPPORTED_PROVIDERS[0].value

    _step("mode")
    wizard_mode = _choose(
        "Setup mode",
        [
            Choice(value="quickstart", label="Quickstart"),
            Choice(value="advanced", label="Advanced"),
        ],
        default=defaults["wizard_mode"],
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

    _step("provider")
    provider = PROVIDER_BY_VALUE[
        _choose(
            "Provider",
            [
                Choice(value=provider.value, label=provider.label, group=provider.group)
                for provider in SUPPORTED_PROVIDERS
            ],
            default=default_provider_value,
        )
    ]
    _step("model")
    default_model = defaults["model"]
    if default_model not in {option.value for option in provider.models}:
        default_model = provider.default_model
    model = _choose(
        "Model",
        [Choice(value=option.value, label=option.label) for option in provider.models],
        default=default_model,
    )
    _step("api key")
    default_api_key = (
        defaults["api_key"] if defaults["api_key_env"] == provider.api_key_env else ""
    )
    try:
        api_key = _collect_validated_api_key(
            provider,
            model,
            default_api_key=default_api_key,
            auto_use_saved_key=bool(default_api_key),
        )
    except KeyboardInterrupt:
        _console.print("\n[yellow]Setup cancelled.[/]")
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
        api_key=api_key,
        probes=probes,
    )
    env_path = sync_provider_env(provider=provider, api_key=api_key, model=model)

    _step("integrations")
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
