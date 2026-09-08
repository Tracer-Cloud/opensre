"""Ollama-specific model discovery for the interactive model command."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from config.llm_credentials import resolve_env_credential
from surfaces.interactive_shell.ui import DIM, ERROR, WARNING
from surfaces.shared.llm_setup.catalog import ProviderOption
from surfaces.shared.llm_setup.ollama import (
    OllamaModelDiscoveryError,
    list_available_models,
    model_is_available,
)
from surfaces.shared.terminal.components import llm_loader


def _available_models(provider: ProviderOption, console: Console) -> tuple[str, ...] | None:
    host = resolve_env_credential(provider.api_key_env, default=provider.credential_default)
    try:
        with llm_loader(console, "Detecting available Ollama models"):
            return list_available_models(host)
    except KeyboardInterrupt:
        console.print(f"[{WARNING}]Ollama model detection cancelled.[/]")
        return None
    except OllamaModelDiscoveryError as exc:
        console.print(f"[{ERROR}]{escape(str(exc))}[/]")
        console.print(
            f"[{DIM}]Start Ollama with[/] [bold]ollama serve[/bold][{DIM}], then retry.[/]"
        )
        return None


def model_menu_choices(
    provider: ProviderOption,
    console: Console,
) -> list[tuple[str, str]] | None:
    """Build model choices from the configured Ollama instance."""
    available_models = _available_models(provider, console)
    if available_models is None:
        return None
    if not available_models:
        console.print(f"[{ERROR}]No Ollama models are available.[/]")
        console.print(
            f"[{DIM}]Pull one with[/] [bold]ollama pull <model>[/bold] "
            f"[{DIM}]or choose another provider.[/]"
        )
        return None
    return [(model, model) for model in available_models]


def validate_model_available(
    provider: ProviderOption,
    model: str,
    console: Console,
) -> bool:
    """Require the selected model to exist on the configured Ollama instance."""
    available_models = _available_models(provider, console)
    if available_models is None:
        return False
    if model_is_available(model, available_models):
        return True

    listed = ", ".join(available_models) or "none"
    console.print(f"[{ERROR}]Ollama model is not available:[/] {escape(model)}")
    console.print(f"[{DIM}]available models:[/] {escape(listed)}")
    console.print(
        f"[{DIM}]Pull it with[/] [bold]ollama pull {escape(model)}[/bold][{DIM}], then retry.[/]"
    )
    return False


__all__ = ["model_menu_choices", "validate_model_available"]
