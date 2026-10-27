"""Connect a provider without selecting it as the active inference source."""

from __future__ import annotations

import os

import click
from rich.console import Console

from config.env_file import sync_env_values
from config.llm_auth.credentials import resolve_for_request, status, verify
from config.llm_auth.model_catalog import save_models
from config.llm_auth.records import save_provider_auth_record
from surfaces.shared.llm_setup import model_discovery, validation
from surfaces.shared.llm_setup.auth_menu import prompt_auth_provider
from surfaces.shared.llm_setup.auth_profiles import provider_for_profile, resolve_auth_profile
from surfaces.shared.llm_setup.auth_results import AuthSetupResult
from surfaces.shared.llm_setup.auth_service import configure_api_key_provider
from surfaces.shared.llm_setup.authorization import maybe_open_setup_page, present_authorization
from surfaces.shared.llm_setup.catalog import ProviderOption
from surfaces.shared.llm_setup.persist import AuthSetupError
from surfaces.shared.llm_setup.subscription_service import configure_subscription
from surfaces.shared.terminal.components.choice_menu import repl_choose_one, repl_tty_interactive


def _required(prompt: str, *, default: str = "", secret: bool = False) -> str:
    if not repl_tty_interactive():
        raise AuthSetupError(
            f"{prompt} is required. Supply it explicitly before non-interactive setup."
        )
    return str(click.prompt(prompt, default=default, hide_input=secret)).strip()


def _provider_settings(provider: ProviderOption) -> dict[str, str]:
    values: dict[str, str] = {}
    if provider.endpoint_env:
        key = provider.endpoint_env
        values[key] = os.getenv(key, "").strip() or _required(key)
    if provider.api_version_env and os.getenv(provider.api_version_env):
        values[provider.api_version_env] = os.environ[provider.api_version_env]
    return values


def _connect_cli(provider: ProviderOption, console: Console) -> AuthSetupResult:
    if provider.adapter_factory is None:
        raise AuthSetupError(f"No CLI adapter is registered for {provider.label}.")
    adapter = provider.adapter_factory()
    while True:
        probe = adapter.detect()
        if probe.installed and probe.logged_in is not False:
            detail = (
                probe.detail if probe.logged_in else f"{probe.detail} Auth will be checked on use."
            )
            save_provider_auth_record(
                provider=provider.value,
                auth_name=provider.value,
                kind="cli",
                source="cli",
                detail=detail,
                verified=probe.logged_in is True,
            )
            return AuthSetupResult(
                provider.value,
                os.getenv(provider.model_env, "") or provider.default_model,
                "cli",
                detail,
                None,
            )
        hint = adapter.auth_hint if probe.installed else adapter.install_hint
        if not repl_tty_interactive():
            raise AuthSetupError(f"{probe.detail} {hint}")
        console.print(f"{probe.detail}\n{hint}", markup=False)
        if (
            repl_choose_one(
                title=f"Connect {provider.label}",
                choices=[("retry", "Retry after install or sign-in"), ("cancel", "Cancel")],
            )
            != "retry"
        ):
            raise click.Abort()


def connect_provider(
    console: Console,
    provider_name: str | None = None,
    *,
    api_key: str | None = None,
    model: str | None = None,
    validate: bool = True,
    open_browser: bool = True,
    profile_id: str | None = None,
    new_account: bool = False,
) -> AuthSetupResult:
    """Persist authentication, then let the caller offer explicit model selection."""
    profile = resolve_auth_profile(provider_name) if provider_name else prompt_auth_provider()
    if profile is None:
        raise click.Abort()
    provider = provider_for_profile(profile)
    settings = _provider_settings(provider)
    # Endpoint discovery/validation needs the draft settings; restore them on failure.
    previous = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    try:
        if profile.kind == "subscription":

            def choose(available: list[tuple[str, str]]) -> str:
                if not available:
                    raise AuthSetupError(f"{profile.label} has no available models.")
                preferred = model or os.getenv(provider.model_env, "") or provider.default_model
                return preferred if preferred in dict(available) else available[0][0]

            result = configure_subscription(
                profile=profile,
                model=model,
                set_provider=False,
                present=present_authorization(open_browser=open_browser, provider=provider.value),
                choose_model=choose,
                profile_id=profile_id,
                new_account=new_account,
                credential=api_key,
            )
        elif profile.kind == "api_key":
            auth = status(provider.value)
            if api_key is None and auth.configured and not auth.stale:
                auth = verify(provider.value)
            reuse = api_key is None and auth.configured and not auth.stale
            key = api_key
            if not reuse and key is None:
                maybe_open_setup_page(profile, enabled=open_browser)
                key = _required(provider.api_key_env, secret=True)
            if key is not None:
                previous[provider.api_key_env] = os.environ.get(provider.api_key_env)
                os.environ[provider.api_key_env] = key.strip()
            available = model_discovery.discover_models(provider)
            preferred = model or os.getenv(provider.model_env, "")
            if not preferred:
                if available:
                    preferred = (
                        provider.default_model
                        if provider.default_model in dict(available)
                        else available[0][0]
                    )
                elif available is not None:
                    preferred = _required("Model or deployment ID")
                else:
                    preferred = provider.default_model
            check_model = preferred or _required("Model ID")
            if reuse:
                if validate:
                    resolved = resolve_for_request(provider.value)
                    checked = validation.validate_provider_credentials(
                        provider=provider, api_key=resolved.api_key, model=check_model
                    )
                    if not checked.ok:
                        raise AuthSetupError(checked.detail)
                result = AuthSetupResult(
                    provider.value, check_model, str(auth.source), auth.detail, None
                )
            else:
                assert key is not None
                result = configure_api_key_provider(
                    profile=profile,
                    api_key=key,
                    model=check_model,
                    set_provider=False,
                    validate=validate,
                )
                settings[provider.api_key_env] = key.strip()
            if available is not None:
                save_models(provider.value, available)
            elif not provider.models:
                save_models(provider.value, [(check_model, check_model)])
        elif profile.kind == "cli":
            result = _connect_cli(provider, console)
        else:
            if profile.kind == "host":
                previous[provider.api_key_env] = os.environ.get(provider.api_key_env)
                host = os.getenv(provider.api_key_env, "") or provider.credential_default
                if repl_tty_interactive():
                    host = _required(provider.api_key_env, default=host)
                settings[provider.api_key_env] = host
                os.environ[provider.api_key_env] = host
            auth = status(provider.value)
            if not auth.configured:
                raise AuthSetupError(auth.detail)
            discovered = model_discovery.discover_models(provider)
            if discovered:
                save_models(provider.value, discovered)
            save_provider_auth_record(
                provider=provider.value,
                auth_name=profile.name,
                kind=profile.kind,
                source=str(auth.source),
                detail=auth.detail,
                verified=auth.verified,
            )
            result = AuthSetupResult(
                provider.value, model or provider.default_model, str(auth.source), auth.detail, None
            )
        if settings:
            sync_env_values(settings)
            os.environ.update(settings)
        from surfaces.shared.llm_setup.handoff import notify_setup

        notify_setup(connected=provider.value, written_keys=tuple(settings))
        return result
    except BaseException:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        raise


def prompt_connection(console: Console) -> str | None:
    """Connect from a model picker, returning there on cancellation or setup failure."""
    try:
        return connect_provider(console).provider
    except (click.Abort, KeyboardInterrupt):
        console.print("Connection cancelled.")
        return None
    except (AuthSetupError, RuntimeError, OSError) as exc:
        console.print(str(exc) or "Connection cancelled.", markup=False)
        return None
