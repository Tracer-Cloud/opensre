"""Connect then optionally select: the shared terminal setup journey."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

import click
from rich.console import Console
from rich.text import Text

from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE
from config.llm_credentials import resolve_env_credential
from surfaces.shared.llm_setup.auth_menu import prompt_auth_provider
from surfaces.shared.llm_setup.auth_profiles import ProviderAuthProfile, provider_for_profile
from surfaces.shared.llm_setup.auth_results import AuthSetupResult
from surfaces.shared.llm_setup.authorization import present_authorization, present_setup_page
from surfaces.shared.llm_setup.connection import connect_provider
from surfaces.shared.llm_setup.handoff import report_setup
from surfaces.shared.llm_setup.model_picker import model_picker
from surfaces.shared.llm_setup.persist import AuthSetupError
from surfaces.shared.llm_setup.selection import SelectionResult, select_provider
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import (
    InputField,
    InputFormError,
    build_input_form,
)


def _endpoint_settings(profile: ProviderAuthProfile, *, interactive: bool) -> dict[str, str]:
    provider = provider_for_profile(profile)
    keys = tuple(
        key
        for key in (
            provider.endpoint_env,
            provider.api_version_env,
            provider.api_key_env if profile.kind == "local" else "",
        )
        if key
    )
    values = {key: os.getenv(key, "").strip() for key in keys}
    missing = [key for key in keys if not values[key] and key != provider.api_version_env]
    if not missing:
        return {key: value for key, value in values.items() if value}
    if not interactive:
        raise AuthSetupError(
            f"Set {', '.join(missing)} before login, or run opensre auth login {provider.value} in a terminal."
        )
    from urllib.parse import urlsplit

    def validate(draft: dict[str, str]) -> dict[str, str]:
        for key in missing:
            parsed = urlsplit(draft[key])
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise InputFormError(
                    "Enter an HTTP(S) URL without embedded credentials.", field=key
                )
        return {key: value.strip() for key, value in draft.items() if value.strip()}

    fields = tuple(
        InputField(
            key,
            key,
            description="Required endpoint URL" if key in missing else "Optional API version",
            required=key in missing,
        )
        for key in keys
    )
    for key in missing:
        values[key] = provider.credential_default
    app = build_input_form(
        values,
        title=f"Connect {provider.label}",
        heading="Provider endpoint settings",
        submit_label="Continue",
        fields=lambda _: fields,
        validate=validate,
        initial_field=missing[0],
    )
    result = run_command_input(app)
    if result is None:
        raise click.Abort()
    return result


def finish_connection(
    console: Console,
    *,
    connect: Callable[[], AuthSetupResult],
    provider: str,
    activation: bool | None,
    model: str | None,
    interactive: bool,
    picker: Callable[..., SelectionResult | None] = model_picker,
) -> SelectionResult | None:
    """Run auth before the user-facing picker; cancellation retains the connection."""
    result = connect()
    replaced = result.source in {"local-file", "fallback"}
    report_setup(provider, activated=False, credential_replaced=replaced)
    from core.llm.factory import reset_llm_clients

    reset_llm_clients()
    console.print(Text(f"Connected: {provider_for_profile_name(provider)}"))
    console.print(Text(result.detail))
    if activation is False:
        console.print(
            Text(f"Connected. Select explicitly with opensre model set {provider} <model>.")
        )
        return None
    if activation is True and (model is not None or not interactive):
        selected = select_provider(provider, model if model is not None else result.model)
        report_setup(provider, activated=True, credential_replaced=replaced)
        return selected
    if interactive:
        picked = picker(console, provider_id=provider, preferred_model=model)
        if picked is not None:
            report_setup(provider, activated=True, credential_replaced=replaced)
        return picked
    console.print(Text(f"Connected. Select explicitly with opensre model set {provider} <model>."))
    return None


def provider_for_profile_name(provider: str) -> str:
    from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile

    return resolve_auth_profile(provider).label


def login_flow(
    console: Console,
    *,
    profile: ProviderAuthProfile | None = None,
    api_key: str | None = None,
    model: str | None = None,
    activation: bool | None = None,
    validate: bool = True,
    open_browser: bool = True,
    remote_authorization: bool = False,
    profile_id: str | None = None,
    new_account: bool = False,
    interactive: bool | None = None,
) -> SelectionResult | None:
    interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
    if profile is None:
        if not interactive:
            raise AuthSetupError("Specify a provider: opensre auth login <provider>.")
        profile = prompt_auth_provider()
        if profile is None:
            return None
    provider = provider_for_profile(profile)
    settings = _endpoint_settings(profile, interactive=interactive)
    needs_key = profile.kind == "api_key" or (
        profile.kind == "subscription"
        and SUBSCRIPTION_BY_VALUE[provider.value].auth_method == "api_key"
    )
    if needs_key and api_key is None:
        api_key = resolve_env_credential(provider.api_key_env) if provider.api_key_env else None
        if not api_key:
            if not interactive:
                raise AuthSetupError(f"Pass --api-key or set {provider.api_key_env} before login.")
            if profile.setup_url:
                present_setup_page(
                    label=profile.label, url=profile.setup_url, open_browser=open_browser
                )
            api_key = click.prompt(f"{profile.label} key", hide_input=True)
    if profile.kind != "subscription" and (profile_id or new_account):
        raise AuthSetupError("--profile and --new-account apply to subscription login.")
    if profile_id and new_account:
        raise AuthSetupError("Use --profile or --new-account, not both.")
    if profile.kind == "subscription" and api_key is not None and not needs_key:
        raise AuthSetupError(f"{profile.label} uses oauth authorization, not an API key.")
    if provider.endpoint_env and not model:
        from surfaces.shared.llm_setup.model_inventory import saved_model

        model = saved_model(provider) or None
        if model is None and interactive:
            app = build_input_form(
                {"model": ""},
                title="Connection validation",
                heading="Required model / deployment ID",
                submit_label="Continue",
                fields=lambda _: (InputField("model", "Exact model ID", required=True),),
                validate=lambda values: values["model"].strip(),
                initial_field="model",
            )
            model = run_command_input(app)
            if model is None:
                return None
        if model is None:
            raise AuthSetupError("Pass --model <exact model or deployment ID> for this endpoint.")
    if profile_id or new_account:
        console.print(
            Text("This explicit account option changes the account used by this provider.")
        )
    return finish_connection(
        console,
        connect=lambda: connect_provider(
            profile=profile,
            model=model,
            api_key=api_key,
            validate=validate,
            settings=settings,
            present=present_authorization(
                open_browser=open_browser and interactive, provider=provider.value
            )
            if profile.kind == "subscription"
            else lambda _: None,
            allow_authorization=interactive or remote_authorization,
            profile_id=profile_id,
            new_account=new_account,
        ),
        provider=provider.value,
        activation=activation,
        model=model,
        interactive=interactive,
        picker=model_picker,
    )


def connect_from_picker(console: Console, provider_id: str | None = None) -> SelectionResult | None:
    from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile

    return login_flow(console, profile=resolve_auth_profile(provider_id) if provider_id else None)
