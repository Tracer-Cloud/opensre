"""Provider/model switch helpers for the /model slash command."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from surfaces.interactive_shell.command_registry.model.presentation import render_current_models
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING


def _provider_allows_custom_models(provider: object) -> bool:
    return bool(getattr(provider, "allow_custom_models", False))


def switch_llm_provider(
    provider_name: str,
    console: Console,
    model: str | None = None,
    *,
    toolcall_model: str | None = None,
) -> bool:
    """Render the result of the shared non-interactive provider selection."""
    from surfaces.shared.llm_setup.auth_results import SelectionError
    from surfaces.shared.llm_setup.selection import select_provider

    try:
        result = select_provider(provider_name, model, toolcall_model=toolcall_model)
    except KeyboardInterrupt:
        console.print("Model selection cancelled. Active model unchanged.")
        return False
    except (SelectionError, RuntimeError, OSError) as exc:
        console.print(f"[{ERROR}]{escape(str(exc))}[/]")
        return False
    console.print(f"[{HIGHLIGHT}]switched LLM provider:[/] {result.provider}")
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    provider = PROVIDER_BY_VALUE.get(result.provider)
    model_env = provider.model_env if provider is not None else "OpenSRE account"
    console.print(
        f"[{HIGHLIGHT}]reasoning model:[/] {escape(result.model) or 'provider default'} ({model_env})"
    )
    if result.toolcall_model:
        console.print(f"[{HIGHLIGHT}]toolcall model:[/] {escape(result.toolcall_model)}")
    console.print(f"[{DIM}]updated {result.env_path}[/]")
    render_current_models(console)
    return True


def switch_toolcall_model(
    toolcall_model: str, console: Console, *, provider_name: str | None = None
) -> bool:
    """Render a shared tool-call model update."""
    from surfaces.shared.llm_setup.auth_results import SelectionError
    from surfaces.shared.llm_setup.selection import select_toolcall_model

    try:
        result = select_toolcall_model(toolcall_model, provider_name=provider_name)
    except KeyboardInterrupt:
        console.print("Model selection cancelled. Active model unchanged.")
        return False
    except (SelectionError, RuntimeError, OSError) as exc:
        console.print(f"[{WARNING}]{escape(str(exc))}[/]")
        return False
    console.print(f"[{HIGHLIGHT}]toolcall model set to:[/] {escape(result.model)}")
    console.print(f"[{DIM}]updated {result.env_path}[/]")
    render_current_models(console)
    return True


def switch_reasoning_model(
    reasoning_model: str, console: Console, *, provider_name: str | None = None
) -> bool:
    """Render a shared reasoning-model update."""
    from surfaces.shared.llm_setup.auth_results import SelectionError
    from surfaces.shared.llm_setup.selection import select_reasoning_model

    try:
        result = select_reasoning_model(reasoning_model, provider_name=provider_name)
    except KeyboardInterrupt:
        console.print("Model selection cancelled. Active model unchanged.")
        return False
    except (SelectionError, RuntimeError, OSError) as exc:
        console.print(f"[{ERROR}]{escape(str(exc))}[/]")
        return False
    console.print(f"[{HIGHLIGHT}]reasoning model set to:[/] {escape(result.model)}")
    console.print(f"[{DIM}]updated {result.env_path}[/]")
    render_current_models(console)
    return True


def restore_default_model(provider_name: str, console: Console) -> bool:
    """Reset a provider to its configured default reasoning model."""
    return switch_llm_provider(provider_name, console)
