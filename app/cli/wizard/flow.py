"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import getpass
import sys
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.cli.wizard.config import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS, ProviderOption
from app.cli.wizard.env_sync import sync_provider_env
from app.cli.wizard.probes import ProbeResult, probe_local_target, probe_remote_target
from app.cli.wizard.store import get_store_path, save_local_config
from app.cli.wizard.validation import build_demo_action_response, validate_provider_credentials

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
) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_row("provider", provider_label)
    table.add_row("model", model)
    table.add_row("target", "local")
    table.add_row("config", saved_path)
    table.add_row("env sync", env_path)
    _console.print(Panel(table, title="Saved configuration", border_style="green"))


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

    _step("Step 1 of 4: Choose mode")
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

    _step("Step 2 of 4: Choose provider")
    provider = _select_provider()
    _step("Step 3 of 4: Choose default model")
    model = _select_model(provider)
    _step("Step 4 of 4: Validate credentials")
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

    _render_saved_summary(
        provider_label=provider.label,
        model=model,
        saved_path=str(saved_path),
        env_path=str(env_path),
    )
    demo_response = build_demo_action_response()
    _render_demo_response(demo_response)
    _render_next_steps()
    return 0
