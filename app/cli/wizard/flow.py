"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import getpass
import sys
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.cli.wizard.config import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS, ProviderOption
from app.cli.wizard.env_sync import sync_env_values, sync_provider_env
from app.cli.wizard.integration_health import (
    IntegrationHealthResult,
    validate_aws_integration,
    validate_datadog_integration,
    validate_grafana_integration,
    validate_slack_webhook,
)
from app.cli.wizard.probes import ProbeResult, probe_local_target, probe_remote_target
from app.cli.wizard.store import get_store_path, save_local_config
from app.cli.wizard.validation import build_demo_action_response, validate_provider_credentials
from app.integrations.store import upsert_integration

_console = Console()


@dataclass(frozen=True)
class Choice:
    """A selectable wizard choice."""

    value: str
    label: str
    group: str | None = None


def _step(title: str) -> None:
    _console.rule(f"[bold cyan]{title}[/]")


def _render_choice_table(prompt: str, choices: list[Choice], default: str | None) -> None:
    _console.print(f"\n[bold]{prompt}[/]")
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Option", style="white")
    table.add_column("Group", style="dim", no_wrap=True)
    table.add_column("Default", style="green", no_wrap=True)

    for index, choice in enumerate(choices, start=1):
        table.add_row(
            str(index),
            choice.label,
            choice.group or "-",
            "yes" if choice.value == default else "",
        )
    _console.print(table)
    if default:
        _console.print("[dim]Press Enter to accept the default.[/]")


def _choose(prompt: str, choices: list[Choice], *, default: str | None = None) -> str:
    _render_choice_table(prompt, choices, default)
    indexed_values: dict[str, str] = {}
    for index, choice in enumerate(choices, start=1):
        indexed_values[str(index)] = choice.value

    prompt_suffix = f" [{default}]" if default else ""
    while True:
        raw_value = input(f"  Enter choice{prompt_suffix}: ").strip()
        if not raw_value and default:
            return default
        selected = indexed_values.get(raw_value)
        if selected:
            return selected
        _console.print("[red]  Invalid choice. Please try again.[/]")


def _choose_many(prompt: str, choices: list[Choice]) -> list[str]:
    _render_choice_table(prompt, choices, default=None)
    _console.print("[dim]Enter comma-separated numbers, or press Enter to skip.[/]")

    indexed_values = {str(index): choice.value for index, choice in enumerate(choices, start=1)}
    while True:
        raw_value = input("  Enter choices: ").strip()
        if not raw_value:
            return []

        parts = [part.strip() for part in raw_value.split(",") if part.strip()]
        selected_values: list[str] = []
        invalid = False
        for part in parts:
            selected = indexed_values.get(part)
            if not selected:
                invalid = True
                break
            if selected not in selected_values:
                selected_values.append(selected)

        if selected_values and not invalid:
            return selected_values
        _console.print("[red]  Invalid selection. Please use comma-separated numbers from the table.[/]")


def _confirm(prompt: str, *, default: bool = True) -> bool:
    default_hint = "Y/n" if default else "y/N"
    accepted = {"y", "yes"}
    rejected = {"n", "no"}
    while True:
        raw_value = input(f"{prompt} [{default_hint}]: ").strip().lower()
        if not raw_value:
            return default
        if raw_value in accepted:
            return True
        if raw_value in rejected:
            return False
        _console.print("[red]Please answer y or n.[/]")


def _prompt_value(
    label: str,
    *,
    default: str = "",
    secret: bool = False,
    allow_empty: bool = False,
) -> str:
    while True:
        hint = f" [{default}]" if default else ""
        prompt = f"  {label}{hint}: "
        value = getpass.getpass(prompt) if secret else input(prompt)
        value = value.strip()
        if value:
            return value
        if default:
            return default
        if allow_empty:
            return ""
        _console.print("[red]  This value is required.[/]")


def _prompt_api_key(provider: ProviderOption) -> str:
    while True:
        value = getpass.getpass(f"\nEnter {provider.label} API key ({provider.api_key_env}): ").strip()
        if value:
            return value
        _console.print("[red]API key is required.[/]")


def _collect_validated_api_key(provider: ProviderOption, model: str) -> str:
    while True:
        api_key = _prompt_api_key(provider)
        with _console.status(f"Validating {provider.label} API key...", spinner="dots"):
            result = validate_provider_credentials(provider=provider, api_key=api_key, model=model)
        if result.ok:
            _console.print(f"[green]  {result.detail}[/]")
            if result.sample_response:
                _console.print(f"  Provider sample: [bold]{result.sample_response}[/]")
            return api_key
        _console.print(f"[red]  Validation failed: {result.detail}[/]")
        _console.print("[dim]  Paste the API key again to retry, or press Ctrl+C to cancel.[/]")


def _select_provider() -> ProviderOption:
    provider_value = _choose(
        "Select your LLM provider:",
        [
            Choice(value=provider.value, label=provider.label, group=provider.group)
            for provider in SUPPORTED_PROVIDERS
        ],
        default=SUPPORTED_PROVIDERS[0].value,
    )
    return PROVIDER_BY_VALUE[provider_value]


def _select_model(provider: ProviderOption) -> str:
    return _choose(
        f"Select the default {provider.label} model:",
        [
            Choice(value=model.value, label=model.label)
            for model in provider.models
        ],
        default=provider.default_model,
    )


def _display_probe(result: ProbeResult) -> None:
    status = "[green]reachable[/]" if result.reachable else "[red]unreachable[/]"
    _console.print(f"  - [bold]{result.target}[/]: {status} [dim]({result.detail})[/]")


def _select_target_for_advanced(local_probe: ProbeResult, remote_probe: ProbeResult) -> str | None:
    _console.print("\n[bold]Reachability status[/]")
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

    _console.print("\n[yellow]Remote configuration is not available yet.[/]")
    if _confirm("Continue with local configuration instead?", default=True):
        return "local"
    _console.print("[yellow]Onboarding cancelled.[/]")
    return None


def _render_header() -> None:
    _console.print(
        Panel(
            "[bold]OpenSRE onboarding[/]\n\n"
            "Configure a local LLM provider, validate the API key, and sync the active settings into this repo.",
            title="Welcome",
            border_style="cyan",
        )
    )


def _render_saved_summary(
    *,
    provider_label: str,
    model: str,
    saved_path: str,
    env_path: str,
    configured_integrations: list[str],
) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_row("provider", provider_label)
    table.add_row("model", model)
    table.add_row("target", "local")
    table.add_row("integrations", ", ".join(configured_integrations) or "none")
    table.add_row("config", saved_path)
    table.add_row("env sync", env_path)
    _console.print(Panel(table, title="Saved configuration", border_style="green"))


def _render_integration_result(service_label: str, result: IntegrationHealthResult) -> None:
    color = "green" if result.ok else "red"
    _console.print(f"[{color}]  {service_label}: {result.detail}[/]")


def _configure_grafana() -> tuple[str, str]:
    while True:
        endpoint = _prompt_value("Grafana instance URL")
        api_key = _prompt_value("Grafana service account token", secret=True)
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
        _console.print("[dim]  Re-enter the Grafana values to try again, or press Ctrl+C to cancel.[/]")


def _configure_datadog() -> tuple[str, str]:
    while True:
        api_key = _prompt_value("Datadog API key", secret=True)
        app_key = _prompt_value("Datadog application key", secret=True)
        site = _prompt_value("Datadog site", default="datadoghq.com")
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
        _console.print("[dim]  Re-enter the Datadog values to try again, or press Ctrl+C to cancel.[/]")


def _configure_slack() -> tuple[str, str]:
    while True:
        webhook_url = _prompt_value("Slack webhook URL", secret=True)
        with _console.status("Validating Slack webhook...", spinner="dots"):
            result = validate_slack_webhook(webhook_url=webhook_url)
        _render_integration_result("Slack", result)
        if result.ok:
            env_path = sync_env_values({"SLACK_WEBHOOK_URL": webhook_url})
            return "Slack", str(env_path)
        _console.print("[dim]  Re-enter the Slack webhook to try again, or press Ctrl+C to cancel.[/]")


def _configure_aws() -> tuple[str, str]:
    auth_mode = _choose(
        "Choose the AWS authentication method:",
        [
            Choice(value="role", label="IAM role ARN"),
            Choice(value="keys", label="Access key + secret"),
        ],
        default="role",
    )

    while True:
        region = _prompt_value("AWS region", default="us-east-1")
        if auth_mode == "role":
            role_arn = _prompt_value("IAM role ARN")
            external_id = _prompt_value("External ID", allow_empty=True)
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
            access_key_id = _prompt_value("AWS access key ID", secret=True)
            secret_access_key = _prompt_value("AWS secret access key", secret=True)
            session_token = _prompt_value("AWS session token", secret=True, allow_empty=True)
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

        _console.print("[dim]  Re-enter the AWS values to try again, or press Ctrl+C to cancel.[/]")


def _configure_selected_integrations() -> tuple[list[str], str | None]:
    selected = _choose_many(
        "Select optional integrations to configure now:",
        [
            Choice(value="grafana", label="Grafana"),
            Choice(value="datadog", label="Datadog"),
            Choice(value="slack", label="Slack"),
            Choice(value="aws", label="AWS"),
        ],
    )
    if not selected:
        return [], None

    configured: list[str] = []
    last_env_path: str | None = None

    handlers = {
        "grafana": _configure_grafana,
        "datadog": _configure_datadog,
        "slack": _configure_slack,
        "aws": _configure_aws,
    }

    for index, service in enumerate(selected, start=1):
        _step(f"Optional integration {index} of {len(selected)}: {service.title()}")
        label, env_path = handlers[service]()
        configured.append(label)
        last_env_path = env_path

    return configured, last_env_path


def _render_demo_response(demo_response: dict) -> None:
    topics = ", ".join(demo_response.get("topics", [])) or "none"
    guidance = demo_response.get("guidance") or []
    summary = [
        f"success: {demo_response.get('success')}",
        f"topics: {topics}",
    ]
    if guidance:
        first = guidance[0]
        summary.append(f"sample topic: {first.get('topic', 'unknown')}")
        content = str(first.get("content", "")).strip().splitlines()
        if content:
            summary.append(f"preview: {content[0][:140]}")
    _console.print(Panel("\n".join(summary), title="Demo action response", border_style="magenta"))


def _render_next_steps() -> None:
    _console.print(
        Panel(
            "1. Run `opensre onboard` any time to update local settings.\n"
            "2. Run `make run -- --input path/to/alert.json` to exercise the CLI.",
            title="Next steps",
            border_style="blue",
        )
    )


def run_wizard(_argv: list[str] | None = None) -> int:
    """Run the interactive wizard."""
    _render_header()

    _step("Step 1 of 5: Choose mode")
    wizard_mode = _choose(
        "Choose setup mode:",
        [
            Choice(value="quickstart", label="QuickStart (always local)"),
            Choice(value="advanced", label="Advanced"),
        ],
        default="quickstart",
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

    _step("Step 2 of 5: Choose provider")
    provider = _select_provider()
    _step("Step 3 of 5: Choose default model")
    model = _select_model(provider)
    _step("Step 4 of 5: Validate credentials")
    try:
        api_key = _collect_validated_api_key(provider, model)
    except KeyboardInterrupt:
        _console.print("\n[yellow]Onboarding cancelled.[/]")
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

    _step("Step 5 of 5: Optional integrations")
    try:
        configured_integrations, integration_env_path = _configure_selected_integrations()
    except KeyboardInterrupt:
        _console.print("\n[yellow]Optional integration setup cancelled. LLM configuration was kept.[/]")
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
