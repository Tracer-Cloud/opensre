"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import logging
import os
from typing import Literal

import questionary

from config.env_file import sync_env_values
from config.llm_auth.model_catalog import save_model_catalog
from config.llm_auth.records import save_provider_auth_record
from config.llm_credentials import resolve_env_credential
from config.setup_store import get_store_path, save_local_config
from infrastructure.terminal.theme import (
    ERROR,
    GLYPH_ERROR,
    GLYPH_WARNING,
    SECONDARY,
    WARNING,
)
from integrations.llm_cli import diagnose_binary_path
from surfaces.cli.wizard.components import (
    Choice,
    WizardBack,
    choose,
    confirm,
    console,
    local_defaults,
    prompt_value,
    step_header,
)
from surfaces.cli.wizard.custom_endpoints import (
    onboarding_provider_choices,
    onboarding_provider_default,
    resolve_onboarding_provider,
)
from surfaces.cli.wizard.endpoint_prompt import (
    ensure_endpoint_settings as ensure_provider_endpoint_settings,
)
from surfaces.cli.wizard.integration_health import IntegrationHealthResult
from surfaces.cli.wizard.llm_credential import (
    CANCEL,
    DEFERRED,
    OK,
    REPICK,
    UNSAVED,
    CredentialState,
    _credential_line_for_saved_summary,
    _persist_llm_credential_with_recovery,
    _prompt_validated_llm_credential,
    _provider_choice_label,
)
from surfaces.cli.wizard.probes import ProbeResult, probe_local_target
from surfaces.cli.wizard.summaries import (
    render_header,
    render_next_steps,
    render_saved_summary,
)
from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile
from surfaces.shared.llm_setup.auth_results import AuthSetupResult
from surfaces.shared.llm_setup.authorization import present_authorization
from surfaces.shared.llm_setup.catalog import (
    PROVIDER_BY_VALUE,
    ProviderOption,
    WizardCredentialKind,
)
from surfaces.shared.llm_setup.discovery import discover_models
from surfaces.shared.llm_setup.login_flow import finish_connection
from surfaces.shared.llm_setup.model_inventory import saved_model
from surfaces.shared.llm_setup.model_picker import model_picker
from surfaces.shared.llm_setup.persist import AuthSetupError
from surfaces.shared.llm_setup.provider_choices import (
    DEFAULT_SETUP_PROVIDER_VALUE,
    OTHER_PROVIDER_SELECTION,
    focused_provider_default,
    focused_setup_provider_options,
    ordered_setup_provider_options,
    other_setup_provider_options,
)
from surfaces.shared.llm_setup.subscription_service import configure_subscription

WIZARD_TOTAL_STEPS = 2
logger = logging.getLogger(__name__)

__all__ = [
    "IntegrationHealthResult",
    "build_demo_action_response",
    "questionary",
    "run_llm_setup",
    "run_wizard",
]


def build_demo_action_response() -> dict:
    """Return a safe built-in action response for onboarding."""
    from tools.system.sre_guidance_tool import get_sre_guidance

    return get_sre_guidance(topic="recovery_remediation", max_topics=1)


def _seed_onboarding_loops() -> int:
    """Seed starter scheduled loops after onboarding completes."""
    from infrastructure.scheduling.scheduler.loops import seed_starter_loops

    try:
        return len(seed_starter_loops())
    except Exception:
        logger.debug("Failed to seed onboarding starter loops", exc_info=True)
        return 0


def _onboarding_provider_options() -> tuple[ProviderOption, ...]:
    return ordered_setup_provider_options()


def _provider_choice(provider: ProviderOption) -> Choice:
    return Choice(
        value=provider.value,
        label=_provider_choice_label(provider),
        hint=provider.group,
    )


def _initial_provider_choices() -> list[Choice]:
    choices = [_provider_choice(provider) for provider in focused_setup_provider_options()]
    choices.append(
        Choice(
            value=OTHER_PROVIDER_SELECTION,
            label="Other LLM provider",
            hint="Show all supported providers",
        )
    )
    return choices


def _choose_onboarding_provider(default_provider_value: str) -> ProviderOption:
    """Prompt for the LLM provider, with the full catalog behind ``Other``."""
    valid_direct_values = {provider.value for provider in _onboarding_provider_options()}
    while True:
        provider_selection = choose(
            "Choose your LLM provider",
            _initial_provider_choices(),
            default=focused_provider_default(default_provider_value),
        )
        if provider_selection in valid_direct_values:
            return PROVIDER_BY_VALUE[
                resolve_onboarding_provider(
                    provider_selection,
                    default=default_provider_value,
                )
            ]
        if provider_selection != OTHER_PROVIDER_SELECTION:
            # Tests and old scripted demos may still feed retired setup-mode
            # answers into the first prompt; ask again instead of crashing.
            continue
        try:
            other_choices = onboarding_provider_choices(
                [_provider_choice(provider) for provider in other_setup_provider_options()]
            )
            other_default = onboarding_provider_default(default_provider_value)
            if other_default not in {choice.value for choice in other_choices}:
                other_provider_values = {
                    provider.value for provider in other_setup_provider_options()
                }
                other_default = next(
                    (
                        choice.value
                        for choice in other_choices
                        if choice.value in other_provider_values
                    ),
                    other_choices[0].value,
                )
            selected = choose(
                "Choose another LLM provider",
                other_choices,
                default=other_default,
                back_on_cancel=True,
            )
            return PROVIDER_BY_VALUE[
                resolve_onboarding_provider(selected, default=default_provider_value)
            ]
        except WizardBack:
            continue


def _run_cli_llm_onboarding(provider: ProviderOption) -> Literal["ok", "abort", "repick"]:
    """Probe CLI binary + auth; recovery menu when missing. ``repick`` = choose another LLM."""
    factory = provider.adapter_factory
    if factory is None:
        console.print(
            f"[{ERROR}]  {GLYPH_ERROR}  Internal error: CLI provider missing adapter factory.[/]"
        )
        return "abort"
    adapter = factory()
    env_key = adapter.binary_env_key
    install_hint = adapter.install_hint
    auth_hint = adapter.auth_hint
    name = adapter.name
    provider_label = provider.label
    for _attempt in range(10):
        probe = adapter.detect()
        if probe.installed and probe.logged_in is True:
            console.print(f"[{SECONDARY}]{probe.detail}[/]")
            return "ok"
        if probe.installed and probe.logged_in is not True:
            console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
            status_prompt = (
                f"{provider_label} requires login. What next?"
                if probe.logged_in is False
                else f"Could not verify {provider_label} login. What next?"
            )
            action = choose(
                status_prompt,
                [
                    Choice(
                        value="retry",
                        label="Re-detect after logging in",
                        hint=auth_hint,
                    ),
                    Choice(
                        value="repick",
                        label="Pick a different LLM provider",
                        hint=None,
                    ),
                ],
                default="retry",
            )
            if action == "repick":
                return "repick"
            continue
        console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
        action = choose(
            f"{provider_label} not found. What next?",
            [
                Choice(
                    value="retry",
                    label="Re-detect after install",
                    hint=install_hint,
                ),
                Choice(
                    value="path",
                    label="Enter full path to the binary",
                    hint=f"Writes {env_key} to .env",
                ),
                Choice(
                    value="repick",
                    label="Pick a different LLM provider",
                    hint=None,
                ),
            ],
            default="retry",
        )
        if action == "repick":
            return "repick"
        if action == "path":
            path = prompt_value(f"Full path to {name} binary")
            reason = diagnose_binary_path(path)
            if reason:
                console.print(f"[{WARNING}]{reason} Try again.[/]")
                continue
            sync_env_values({env_key: path})
            os.environ[env_key] = path
            continue
        console.print(f"[{SECONDARY}]    Hint: {install_hint}[/]")
    console.print(f"[{WARNING}]  {GLYPH_WARNING}  Too many retry attempts. Aborting setup.[/]")
    return "abort"


def run_llm_setup(
    *,
    show_header: bool = True,
    start_step: int = 1,
    total_steps: int = WIZARD_TOTAL_STEPS,
) -> int:
    """Prompt for LLM provider + credential and persist local config.

    ``start_step`` / ``total_steps`` let a surrounding flow (e.g. factory setup)
    place this block after earlier steps without rewriting step headers.
    """
    if show_header:
        render_header()
    defaults = local_defaults()
    saved_provider_value = defaults["provider"] if isinstance(defaults["provider"], str) else None
    saved_model_value = defaults["model"] if isinstance(defaults["model"], str) else ""
    provider_options = _onboarding_provider_options()
    provider_option_values = {p.value for p in provider_options}
    default_provider_value = (
        saved_provider_value
        if saved_provider_value in provider_option_values
        else DEFAULT_SETUP_PROVIDER_VALUE
    )

    store_path = get_store_path()
    local_probe = probe_local_target(store_path)
    remote_probe = ProbeResult(
        target="remote",
        reachable=False,
        detail="Remote setup is configured after first-run LLM setup.",
    )

    force_repick = False
    provider: ProviderOption
    model: str
    credential_state: CredentialState = OK
    # Records a ``continue_unsaved`` secret export so it can be re-applied
    # before the in-process shell handoff.
    session_env_sink: dict[str, str] = {}
    llm_step = start_step
    summary_step = start_step + 1
    while True:
        credential_state = OK
        session_env_sink = {}
        step_header(llm_step, total_steps, "LLM Provider")
        saved_provider = (
            PROVIDER_BY_VALUE.get(saved_provider_value) if saved_provider_value else None
        )
        if saved_provider is not None and not force_repick:
            current_model = saved_model_value or saved_provider.default_model
            console.print(
                f"[{SECONDARY}]current provider  {_provider_choice_label(saved_provider)}  ·  {current_model}[/]"
            )
            change_provider = confirm("Change provider?", default=False)
        else:
            change_provider = True
        force_repick = False

        if change_provider:
            try:
                provider = _choose_onboarding_provider(default_provider_value)
            except WizardBack:
                force_repick = True
                continue
            model = provider.default_model
        else:
            assert saved_provider is not None
            provider = saved_provider
            model = saved_model_value or provider.default_model

        # Authentication uses an internal validation model. Only the shared
        # post-connection picker may change the active selection.
        model = saved_model(provider) or model
        if provider.credential_kind == WizardCredentialKind.SUBSCRIPTION:
            try:
                connected = configure_subscription(
                    profile=resolve_auth_profile(provider.value),
                    model=None,
                    set_provider=False,
                    present=present_authorization(open_browser=True, provider=provider.value),
                )
            except AuthSetupError as exc:
                console.print(str(exc), markup=False)
                return 1
        else:
            if provider.credential_kind == WizardCredentialKind.CLI:
                cli_out = _run_cli_llm_onboarding(provider)
                if cli_out == "abort":
                    return 1
                if cli_out == "repick":
                    force_repick = True
                    continue
            elif provider.credential_kind != WizardCredentialKind.NONE:
                existing = resolve_env_credential(provider.api_key_env)
                legacy = str(defaults.get("legacy_api_key") or "").strip()
                if not existing and legacy and provider.credential_secret:
                    migration = _persist_llm_credential_with_recovery(
                        provider, legacy, session_env_sink=session_env_sink
                    )
                    if migration == CANCEL:
                        return 1
                    if migration == REPICK:
                        force_repick = True
                        continue
                    credential_state = migration
                    existing = legacy
                if existing and provider.credential_secret:
                    # Reuse an existing supported key without a second secret prompt.
                    os.environ[provider.api_key_env] = existing
                else:
                    outcome, model = _prompt_validated_llm_credential(
                        provider,
                        model=model,
                        session_env_sink=session_env_sink,
                        interactive_model=False,
                    )
                    if outcome == CANCEL:
                        return 1
                    if outcome == REPICK:
                        force_repick = True
                        continue
                    credential_state = outcome
            endpoint = ensure_provider_endpoint_settings(provider)
            if endpoint is None:
                force_repick = True
                continue
            if endpoint:
                sync_env_values(endpoint)
                os.environ.update(endpoint)
            if credential_state != DEFERRED:
                try:
                    catalog = discover_models(provider)
                    if catalog is not None:
                        save_model_catalog(provider.value, catalog)
                except (ValueError, RuntimeError) as exc:
                    console.print(str(exc), markup=False)
                    return 1
            if credential_state not in (DEFERRED, UNSAVED):
                save_provider_auth_record(
                    provider=provider.value,
                    auth_name=provider.value,
                    kind=str(provider.credential_kind),
                    source="metadata",
                    detail="Connected during onboarding.",
                    verified=credential_state == OK,
                )
            connected = AuthSetupResult(
                provider.value,
                model,
                "session" if credential_state == UNSAVED else "metadata",
                "Onboarding connection",
                None,
            )

        if credential_state == DEFERRED:
            selected = None
        else:

            def connection_result(result: AuthSetupResult = connected) -> AuthSetupResult:
                return result

            selected = finish_connection(
                console,
                connect=connection_result,
                provider=provider.value,
                activation=None,
                model=None,
                interactive=True,
                picker=model_picker,
            )
        if selected is None:
            # Keep the completed connection and existing route, including on Esc.
            step_header(summary_step, total_steps, "Summary")
            console.print(
                f"Connected {provider.label}; active model unchanged."
                if credential_state != DEFERRED
                else "Setup deferred; active model unchanged.",
                markup=False,
            )
            console.print(
                f"Next: opensre auth login {provider.value} or opensre model", markup=False
            )
            _seed_onboarding_loops()
            return 0
        model = selected.model
        break

    probes = {
        "local": local_probe.as_dict(),
        "remote": remote_probe.as_dict(),
    }
    saved_path = save_local_config(
        wizard_mode="quickstart",
        provider=provider.value,
        model=model,
        api_key_env=provider.api_key_env,
        model_env=provider.model_env,
        probes=probes,
    )
    env_path = selected.env_path
    if credential_state == UNSAVED:
        # Re-apply the session-only value the user chose to continue with so the
        # in-process shell handoff can read it. A ``host`` value normally goes
        # straight to .env and never reaches this sink; it only lands here when
        # its .env write failed and the user picked "continue without saving".
        os.environ.update(session_env_sink)

    _seed_onboarding_loops()

    step_header(summary_step, total_steps, "Summary")
    render_saved_summary(
        provider_label=provider.label,
        model=model,
        saved_path=str(saved_path),
        env_path=str(env_path),
        credential_line=_credential_line_for_saved_summary(
            provider, credential_state=credential_state
        ),
    )
    render_next_steps()
    return 0


def run_wizard(_argv: list[str] | None = None) -> int:
    """Run the interactive LLM-only onboarding wizard."""
    return run_llm_setup()
