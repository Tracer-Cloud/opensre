"""Provider/model switch helpers for the /model slash command."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

from config.constants.llm import LLM_PROVIDER_ENV
from surfaces.interactive_shell.command_registry.model.presentation import render_current_models
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING
from surfaces.shared.terminal.components.choice_menu import print_valid_choice_list


def _account_model_change_is_locked(console: Console) -> bool:
    """Explain and enforce the hosted model lock for signed-in accounts."""
    from config.account import account_llm_route

    route = account_llm_route()
    if route is None:
        return False
    console.print(
        f"[{WARNING}]LLM settings are managed by your OpenSRE account:[/] "
        f"openai ({escape(route.model)}, hosted by OpenSRE)."
    )
    console.print(
        f"[{DIM}]Run[/] [bold]opensre account logout[/bold] "
        f"[{DIM}]before configuring a different provider or model.[/]"
    )
    return True


def _format_supported_models(provider_models: tuple[object, ...]) -> str:
    values = [str(getattr(model, "value", "")) for model in provider_models]
    visible = [value for value in values if value]
    return ", ".join(visible) if visible else "provider default"


def _normalize_model_id(model: str) -> str:
    """Collapse internal whitespace in a model id to single hyphens.

    A model id is a single token, so a value like ``"gpt 5.5"`` is a mis-parsed
    ``"gpt-5.5"``. The CLI path (``/model set gpt 5.5``) already rebuilds the id as
    ``gpt-5.5``; normalizing here keeps the planner/tool path
    (``llm_set_provider`` -> ``switch_reasoning_model``) consistent so a custom-model
    provider (e.g. openai) can't persist a whitespace-bearing slug that later fails
    availability checks and silently falls back.
    """
    return "-".join(model.split())


def _is_model_supported(
    _provider_value: str, model: str, provider_models: tuple[object, ...]
) -> bool:
    supported_values = {str(getattr(option, "value", "")) for option in provider_models}
    return model in supported_values


def _provider_allows_custom_models(provider: object) -> bool:
    return bool(getattr(provider, "allow_custom_models", False))


def _is_model_allowed(provider: object, model: str) -> bool:
    provider_value = str(getattr(provider, "value", ""))
    provider_models = getattr(provider, "models", ())
    if _is_model_supported(provider_value, model, provider_models):
        return True
    return bool(model) and _provider_allows_custom_models(provider)


def _resolve_omitted_model(provider: object) -> str:
    """Model to persist/display when the caller supplies none (switch/restore-default).

    Custom gateways ship an empty ``default_model`` (the user's gateway serves its
    own names); falling back to it would blank a previously-working model and break
    the next ``LLMSettings`` load. Mirror the missing-base-URL guard: reuse the
    already-configured model from the environment. First-party providers keep their
    real ``default_model``.
    """
    default_model = str(getattr(provider, "default_model", "") or "")
    if default_model:
        return default_model
    from core.llm.providers.custom_endpoints import is_custom_provider

    model_env = str(getattr(provider, "model_env", "") or "")
    if model_env and is_custom_provider(str(getattr(provider, "value", ""))):
        legacy_model_env = str(getattr(provider, "legacy_model_env", "") or "")
        return os.getenv(model_env, "").strip() or (
            os.getenv(legacy_model_env, "").strip() if legacy_model_env else ""
        )
    return default_model


def _reset_runtime_llm_caches() -> None:
    """Force subsequent REPL assistant calls to use the updated model env."""
    from core.llm.factory import reset_llm_clients

    reset_llm_clients()


def switch_llm_provider(
    provider_name: str,
    console: Console,
    model: str | None = None,
    *,
    toolcall_model: str | None = None,
) -> bool:
    if _account_model_change_is_locked(console):
        return False

    from config.llm_auth.credentials import status as credential_status
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
    from surfaces.shared.llm_setup.env_sync import sync_provider_env

    provider_key = provider_name.strip().lower()
    provider = PROVIDER_BY_VALUE.get(provider_key)
    if provider is None:
        console.print(f"[{ERROR}]unknown LLM provider:[/] {escape(provider_name)}")
        print_valid_choice_list(
            console,
            title="valid providers:",
            choices=sorted(PROVIDER_BY_VALUE),
        )
        return False

    # Refuse to half-update .env when prompt-safe status says the target
    # provider has no credential path. Stale metadata gets a warning, because
    # confirming it requires an intentional request-time credential read.
    auth_status = credential_status(provider.value)
    if provider.value == "azure-openai":
        from core.llm.providers.azure_openai import azure_openai_endpoint_configured

        if not azure_openai_endpoint_configured():
            console.print(
                f"[{ERROR}]missing Azure OpenAI endpoint config:[/] "
                "set AZURE_OPENAI_BASE_URL, or run [bold]opensre onboard[/bold]."
            )
            return False
    from core.llm.providers.custom_endpoints import is_custom_provider

    if (
        is_custom_provider(provider.value)
        and provider.endpoint_env
        and not os.getenv(provider.endpoint_env, "").strip()
    ):
        console.print(
            f"[{ERROR}]missing base URL for {provider.value}:[/] "
            f"set {provider.endpoint_env}, or run [bold]opensre onboard[/bold]."
        )
        return False
    if provider.credential_secret and provider.api_key_env and not auth_status.configured:
        console.print(
            f"[{ERROR}]missing credential for {provider.value}:[/] "
            f"{provider.api_key_env} is not set."
        )
        if not getattr(console, "is_terminal", False):
            # Non-interactive (script/headless): no stdin to prompt on.
            console.print(
                f"[{DIM}]set it with[/] [bold]export {provider.api_key_env}=<your-key>[/bold] "
                f"[{DIM}]or run[/] [bold]opensre auth login {provider.value}[/bold] "
                f"[{DIM}]to save it, then rerun this command.[/]"
            )
            return False
        api_key = console.input(
            f"[{HIGHLIGHT}]paste your {provider.api_key_env} (blank to cancel)> [/]",
            password=True,
        ).strip()
        if not api_key:
            console.print(
                f"[{DIM}]cancelled — set it later with[/] "
                f"[bold]opensre auth login {provider.value}[/bold][{DIM}].[/]"
            )
            return False
        from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile
        from surfaces.shared.llm_setup.auth_service import (
            AuthSetupError,
            configure_api_key_provider,
        )

        console.print(f"[{DIM}]validating {provider.value} key…[/]")
        try:
            configure_api_key_provider(
                profile=resolve_auth_profile(provider.value),
                api_key=api_key,
                set_provider=False,
            )
        except (AuthSetupError, KeyError) as exc:
            console.print(f"[{ERROR}]could not save {provider.api_key_env}:[/] {escape(str(exc))}")
            return False
        console.print(f"[{DIM}]saved {provider.api_key_env}.[/]")
        auth_status = credential_status(provider.value)
    if provider.credential_secret and provider.api_key_env and auth_status.stale:
        console.print(
            f"[{WARNING}]credential status for {provider.value} is stale:[/] "
            f"{escape(auth_status.detail)}"
        )
        console.print(
            f"[{DIM}]run[/] [bold]opensre auth verify {provider.value}[/bold] "
            f"[{DIM}]to refresh metadata if the next LLM request fails.[/]"
        )

    selected_model = _normalize_model_id(model) if model else _resolve_omitted_model(provider)
    if is_custom_provider(provider.value) and not selected_model:
        # Custom gateways require a model; refuse rather than persist a blank one
        # (which would break the next config load), mirroring the base-URL guard.
        console.print(
            f"[{ERROR}]missing model for {provider.value}:[/] pass one "
            f"(e.g. [bold]/model set {provider.value} <model>[/bold]), set "
            f"{provider.model_env}, or run [bold]opensre onboard[/bold]."
        )
        return False
    if selected_model and not _is_model_allowed(provider, selected_model):
        console.print(f"[{ERROR}]unknown model for {provider.value}:[/] {escape(selected_model)}")
        console.print(
            f"[{DIM}]known reasoning models:[/] {escape(_format_supported_models(provider.models))}"
        )
        return False

    selected_toolcall: str | None = None
    if toolcall_model is not None:
        if not provider.toolcall_model_env:
            console.print(
                f"[{WARNING}]provider {provider.value} does not expose a separate "
                "toolcall model[/] — toolcall override ignored."
            )
        else:
            selected_toolcall = _normalize_model_id(toolcall_model)
            if selected_toolcall and not _is_model_allowed(provider, selected_toolcall):
                console.print(
                    f"[{ERROR}]unknown model for {provider.value}:[/] {escape(selected_toolcall)}"
                )
                console.print(
                    f"[{DIM}]known toolcall models:[/] "
                    f"{escape(_format_supported_models(provider.models))}"
                )
                return False

    env_path = sync_provider_env(
        provider=provider,
        model=selected_model,
        toolcall_model=selected_toolcall or None,
    )
    _reset_runtime_llm_caches()

    # Be explicit about which slot each model lands in.
    console.print(f"[{HIGHLIGHT}]switched LLM provider:[/] {provider.value}")
    console.print(
        f"[{HIGHLIGHT}]reasoning model:[/] {selected_model or 'provider default'} "
        f"[{DIM}]({provider.model_env})[/]"
    )
    if selected_toolcall:
        console.print(
            f"[{HIGHLIGHT}]toolcall model:[/] {selected_toolcall} "
            f"[{DIM}]({provider.toolcall_model_env})[/]"
        )
    console.print(f"[{DIM}]updated {env_path}[/]")
    render_current_models(console)
    return True


def switch_toolcall_model(
    toolcall_model: str,
    console: Console,
    *,
    provider_name: str | None = None,
) -> bool:
    """Set the toolcall model for the active (or named) provider."""
    if _account_model_change_is_locked(console):
        return False

    from config.env_file import sync_env_values
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    raw_name = provider_name if provider_name else os.getenv(LLM_PROVIDER_ENV, "anthropic")
    resolved_name = (raw_name or "anthropic").strip().lower()
    provider = PROVIDER_BY_VALUE.get(resolved_name)
    if provider is None:
        console.print(f"[{ERROR}]unknown LLM provider:[/] {escape(resolved_name)}")
        print_valid_choice_list(
            console,
            title="valid providers:",
            choices=sorted(PROVIDER_BY_VALUE),
        )
        return False
    if not provider.toolcall_model_env:
        console.print(
            f"[{WARNING}]provider {provider.value} does not expose a separate "
            "toolcall model[/] — nothing to set."
        )
        return False
    new_model = _normalize_model_id(toolcall_model)
    if not new_model:
        console.print(f"[{ERROR}]toolcall model cannot be empty[/]")
        return False

    values = {provider.toolcall_model_env: new_model}
    env_path = sync_env_values(values)
    os.environ.update(values)
    _reset_runtime_llm_caches()

    console.print(
        f"[{HIGHLIGHT}]toolcall model set to:[/] {new_model} "
        f"[{DIM}]({provider.value} · {provider.toolcall_model_env})[/]"
    )
    console.print(f"[{DIM}]updated {env_path}[/]")
    render_current_models(console)
    return True


def switch_reasoning_model(
    reasoning_model: str,
    console: Console,
    *,
    provider_name: str | None = None,
) -> bool:
    """Set the reasoning model for the active (or named) provider."""
    if _account_model_change_is_locked(console):
        return False

    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
    from surfaces.shared.llm_setup.env_sync import sync_reasoning_model_env

    raw_name = provider_name if provider_name else os.getenv(LLM_PROVIDER_ENV, "anthropic")
    resolved_name = (raw_name or "anthropic").strip().lower()
    provider = PROVIDER_BY_VALUE.get(resolved_name)
    if provider is None:
        console.print(f"[{ERROR}]unknown LLM provider:[/] {escape(resolved_name)}")
        print_valid_choice_list(
            console,
            title="valid providers:",
            choices=sorted(PROVIDER_BY_VALUE),
        )
        return False

    new_model = _normalize_model_id(reasoning_model)
    if not new_model:
        console.print(f"[{ERROR}]reasoning model cannot be empty[/]")
        return False
    if not _is_model_allowed(provider, new_model):
        console.print(f"[{ERROR}]unknown model for {provider.value}:[/] {escape(new_model)}")
        console.print(
            f"[{DIM}]known reasoning models:[/] {escape(_format_supported_models(provider.models))}"
        )
        return False

    env_path = sync_reasoning_model_env(provider=provider, model=new_model)
    _reset_runtime_llm_caches()

    console.print(
        f"[{HIGHLIGHT}]reasoning model set to:[/] {new_model} "
        f"[{DIM}]({provider.value} · {provider.model_env})[/]"
    )
    console.print(f"[{DIM}]updated {env_path}[/]")
    render_current_models(console)
    return True


def restore_default_model(provider_name: str, console: Console) -> bool:
    """Reset a provider to its configured default reasoning model."""
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    provider_key = provider_name.strip().lower()
    provider = PROVIDER_BY_VALUE.get(provider_key)
    if provider is None:
        console.print(f"[{ERROR}]unknown LLM provider:[/] {escape(provider_name)}")
        print_valid_choice_list(
            console,
            title="valid providers:",
            choices=sorted(PROVIDER_BY_VALUE),
        )
        return False
    return switch_llm_provider(provider.value, console, model=provider.default_model)
