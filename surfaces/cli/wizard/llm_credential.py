"""Onboarding LLM-credential helpers: prompt, validate, persist, and recover.

Extracted from ``surfaces/cli/wizard/flow.py`` so the wizard's top-level control
flow (``run_wizard``) stays readable and this credential-specific logic lives in one
place (#4055 review). ``run_wizard`` imports the public helpers from here — this
module must never import ``flow`` (that would be a cycle); it depends only on the
same lower-level sources ``flow`` uses: ``components``, ``validation``, ``env_sync``,
``config``, and the shared theme.
"""

from __future__ import annotations

import os
from typing import Final, Literal

from rich.text import Text

from core.llm.providers.azure_openai import is_azure_openai_provider
from infrastructure.terminal.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    GLYPH_WARNING,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from surfaces.cli.wizard.azure_openai import (
    choose_provider_model,
)
from surfaces.cli.wizard.components import (
    Choice,
    WizardBack,
    choose,
    console,
    persist_llm_api_key,
    prompt_value,
    step,
)
from surfaces.cli.wizard.endpoint_prompt import (
    ensure_endpoint_settings as ensure_provider_endpoint_settings,
)
from surfaces.shared.llm_setup.catalog import (
    PROJECT_ENV_PATH,
    ProviderOption,
    WizardCredentialKind,
)
from surfaces.shared.llm_setup.env_sync import sync_env_values
from surfaces.shared.llm_setup.validation import ValidationResult, validate_provider_credentials

#: What became of the credential the wizard just collected. ``unsaved`` outranks
#: ``unverified``: a credential that never landed anywhere is the more urgent thing
#: to say on the summary screen.
CredentialState = Literal["ok", "unverified", "unsaved", "deferred"]
CredentialOutcome = Literal["ok", "unverified", "unsaved", "deferred", "repick", "cancel"]

# Recovery/outcome vocabulary. These are the byte-identical string values the wizard
# menus, ``choose`` defaults, and ``run_wizard`` branches all resolve against; named
# constants keep the vocabulary spelled in exactly one place (#4055 review). The string
# values must never change — tests assert against them and ``choose(default=...)``
# resolves ``default`` against ``Choice.value``.
RETRY: Final = "retry"
REPICK: Final = "repick"
CANCEL: Final = "cancel"
SAVE_ANYWAY: Final = "save_anyway"
CONTINUE_UNSAVED: Final = "continue_unsaved"
#: Credential-state values (mirror the ``CredentialState`` literal above).
OK: Final = "ok"
UNSAVED: Final = "unsaved"
UNVERIFIED: Final = "unverified"
#: The user has no key to hand. Onboarding finishes; the key is added later with
#: ``opensre auth login <provider>`` or the provider env var. One value for both
#: the prompt outcome and the recorded state, as with ``unsaved``/``unverified``.
DEFERRED: Final = "deferred"

_LLM_CREDENTIAL_MAX_ATTEMPTS = 10  # mirrors _run_cli_llm_onboarding's retry budget

#: Row 3 of every credential-recovery menu. The label is byte-identical to the CLI
#: onboarding menus so "pick a different LLM provider" reads the same everywhere, and
#: it drives the existing ``force_repick`` mechanism in ``run_wizard``.
_REPICK_CHOICE = Choice(
    value=REPICK,
    label="Pick a different LLM provider",
    hint="Returns to the provider and model picks",
)


def _provider_choice_label(provider: ProviderOption) -> str:
    if provider.value == "claude-code":
        return "Claude Code"
    if provider.value == "openai":
        return "OpenAI"
    if provider.value == "openrouter":
        return "OpenRouter"
    if provider.value == "anthropic":
        return "Anthropic"
    return provider.label


def _credential_prompt_label(provider: ProviderOption) -> str:
    """Provider label without the credential kind when the choice already includes it."""
    suffix = f" {provider.credential_label}"
    if provider.label.lower().endswith(suffix.lower()):
        return provider.label[: -len(suffix)]
    return provider.label


def _credential_line_for_saved_summary(
    provider: ProviderOption,
    *,
    credential_state: CredentialState = OK,
) -> str:
    """One-line credential description for the post-wizard saved summary.

    ``credential_state`` keeps this line honest: the summary must not print
    the credentials file right after the wizard warned that the credential was
    never saved, or that it was saved without being verified.
    """
    if provider.credential_kind == WizardCredentialKind.SUBSCRIPTION:
        return "subscription credentials"
    if provider.credential_kind == WizardCredentialKind.HOST:
        # A ``host`` credential (e.g. the Ollama host URL) is written to the project
        # ``.env`` by ``_persist_llm_credential``, never the credentials file — the
        # summary must name .env in both the verified and unverified cases.
        if credential_state == UNVERIFIED:
            return "project .env (unverified)"
        return "project .env"
    if provider.credential_kind != WizardCredentialKind.CLI:
        if credential_state == DEFERRED:
            return f"not set yet — run `opensre auth login {provider.value}` when you have a key"
        if credential_state == UNSAVED:
            return "not saved — re-enter next run"
        if credential_state == UNVERIFIED:
            return "local credentials file (~/.opensre/credentials.json, unverified)"
        return "local credentials file (~/.opensre/credentials.json)"
    if provider.adapter_factory is None:
        return f"{provider.label} (CLI)"
    cli_adapter = provider.adapter_factory()
    return f"{provider.label} ({cli_adapter.auth_hint})"


def _persist_llm_credential(provider: ProviderOption, value: str) -> bool:
    """Persist one prompted credential where the runtime will actually read it.

    ``credential_kind == WizardCredentialKind.HOST`` values (e.g. the Ollama host URL) are plain
    runtime configuration, not secrets: the runtime resolves them from the
    environment only, never the keyring, so they belong in the project ``.env``.
    Everything else is written to the credentials file and mirrored to ``.env``.

    Returns ``False`` on a failed write so ``_persist_llm_credential_with_recovery``
    can offer the shared recovery menu. A host ``.env`` write that raises ``OSError``
    (permission denied, read-only fs, full disk) fails soft exactly like a keyring
    failure — it never propagates and crashes onboarding (#3591).
    """
    if provider.credential_kind == WizardCredentialKind.HOST:
        try:
            sync_env_values({provider.api_key_env: value})
        except OSError as exc:
            # Same remediation style the keyring branch uses: a WARNING line naming the
            # target and the error, then return False so the caller shows the shared
            # recovery menu instead of letting the write error escape (#3591).
            console.print(
                f"[{WARNING}]  {GLYPH_WARNING}  "
                f"Could not write {provider.api_key_env} to {PROJECT_ENV_PATH}: {exc}[/]"
            )
            return False
        os.environ[provider.api_key_env] = value
        return True
    return persist_llm_api_key(provider.api_key_env, value)


def _recovery_action(*, prompt: str, retry_label: str, retry_hint: str, escape: Choice) -> str:
    """The single recovery menu, shared by both LLM-credential failure sites.

    Always three rows, always this order: retry (the default), the caller's escape
    hatch, then repick. Returns ``"retry"``, ``escape.value``, ``"repick"`` or
    ``"cancel"``.

    ``choose`` is called without ``back_on_cancel``, so ESCAPE here raises a bare
    ``KeyboardInterrupt`` and never ``WizardBack`` — there is no back-navigation arm
    to catch.
    """
    try:
        return choose(
            prompt,
            [
                # ``RETRY`` must stay row 1's value: choose resolves ``default`` against
                # Choice.value and raises ValueError when it matches no row.
                Choice(value=RETRY, label=retry_label, hint=retry_hint),
                escape,
                _REPICK_CHOICE,
            ],
            default=RETRY,
        )
    except KeyboardInterrupt:
        console.print(f"\n[{WARNING}]Setup cancelled.[/]")
        return CANCEL


def _render_credential_validation(label: str, model: str, result: ValidationResult) -> None:
    """Render the live-probe outcome, naming the provider *and* the probed model.

    The model is on the status line on purpose: it is what makes a probe against the
    wrong model visible instead of silent.
    """
    ok = result.ok
    status_line = Text()
    status_line.append(
        f"  {GLYPH_SUCCESS if ok else GLYPH_ERROR}  ",
        style=f"bold {HIGHLIGHT}" if ok else f"bold {ERROR}",
    )
    status_line.append(label, style=f"bold {TEXT}")
    status_line.append("  ·  ", style=DIM)
    status_line.append(model, style=BRAND)
    status_line.append("  ·  ", style=DIM)
    status_line.append("Connected" if ok else "Failed", style=TEXT)
    console.print(status_line)

    # The validator's detail is rendered verbatim, never reworded and never branched
    # on: only its auth path says "rejected", so an offline user is never accused of
    # holding a bad credential.
    for raw_line in result.detail.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        detail_text = Text()
        detail_text.append(f"     {line}", style=SECONDARY)
        console.print(detail_text)
    if result.sample_response:
        reply = Text()
        reply.append(f"     Test reply: {result.sample_response}", style=SECONDARY)
        console.print(reply)


def _validate_llm_credential(
    provider: ProviderOption, *, value: str, model: str
) -> ValidationResult:
    """Run the live provider probe behind a spinner, converting any escape into a result."""
    label = _credential_prompt_label(provider)
    try:
        with console.status(f"Validating {label} {provider.credential_label}...", spinner="dots"):
            return validate_provider_credentials(provider=provider, api_key=value, model=model)
    except Exception as err:  # mirror the validator's own catch-all conversion
        return ValidationResult(ok=False, detail=f"Validation request failed: {err}")


def _persist_llm_credential_with_recovery(
    provider: ProviderOption,
    api_key: str,
    *,
    session_env_sink: dict[str, str] | None = None,
) -> Literal["ok", "unsaved", "repick", "cancel"]:
    """Persist a credential; on write failure offer the shared recovery menu.

    ``retry`` re-runs the *write* with the same value and never re-prompts, which is
    why this is also correct at the legacy-key migration site, where there is no
    prompt to return to.

    Reachable for ``credential_kind == WizardCredentialKind.HOST`` only when the ``.env`` write fails:
    ``_persist_llm_credential`` returns ``False`` on an ``OSError`` write error, so the
    host lands on the same recovery menu the keyring path uses (#3591). A successful
    host write returns ``True`` and never reaches this menu.

    ``session_env_sink`` records the ``continue_unsaved`` export ``{env_key: api_key}``
    so ``run_wizard`` can re-apply it before the in-process shell handoff (#3591).
    This is the single point where an unsaved secret is exported, so it is the
    only capture site needed for all callers.
    """
    env_key = provider.api_key_env
    for _attempt in range(_LLM_CREDENTIAL_MAX_ATTEMPTS):
        if _persist_llm_credential(provider, api_key):
            return OK
        action = _recovery_action(
            prompt=f"{env_key} could not be saved. What next?",
            retry_label="Retry saving to the local credentials file",
            retry_hint="Run the steps above first, then retry",
            escape=Choice(
                value=CONTINUE_UNSAVED,
                label="Continue without saving (this session only)",
                hint=f"Exports {env_key} for this run — you must re-enter it next time",
            ),
        )
        if action == RETRY:
            continue
        if action == CONTINUE_UNSAVED:
            # Process-local only. ``continue_unsaved`` must not write the secret to
            # ``.env``. A ``host`` value is not a secret, but the same os.environ-only
            # export is still correct for it.
            os.environ[env_key] = api_key
            if session_env_sink is not None:
                session_env_sink[env_key] = api_key
            console.print(
                f"[{WARNING}]  {GLYPH_WARNING}  "
                f"Using {env_key} for this session only — it was not saved.[/]"
            )
            console.print(
                f"[{SECONDARY}]     Re-enter it next run, "
                f"or fix the keychain with the steps above.[/]"
            )
            return UNSAVED
        if action == REPICK:
            return REPICK
        return CANCEL  # ESCAPE, or defensively: the menu offers no other values
    console.print(f"[{WARNING}]  {GLYPH_WARNING}  Too many retry attempts. Aborting setup.[/]")
    return CANCEL


def _clear_provider_endpoint_env(provider: ProviderOption) -> None:
    """Drop a provider's endpoint env vars so the endpoint is re-prompted next iteration.

    After a validation failure the endpoint the user typed may be the wrong resource
    URL / gateway base URL, but ``ensure_endpoint_settings`` short-circuits while that
    stale value is still in ``os.environ`` — so retry/repick would silently reuse the
    bad endpoint and never ask for a correction. Popping the endpoint env forces a
    fresh prompt. Applies to the endpoint-carrying providers (Azure and the custom
    OpenAI-/Anthropic-compatible gateways); a no-op for everything else (#3591).
    """
    from core.llm.providers.custom_endpoints import is_custom_provider

    if not (is_azure_openai_provider(provider.value) or is_custom_provider(provider.value)):
        return
    for env_key in (provider.endpoint_env, provider.api_version_env):
        if env_key:
            os.environ.pop(env_key, None)


def _prompt_validated_llm_credential(
    provider: ProviderOption,
    *,
    model: str,
    session_env_sink: dict[str, str] | None = None,
) -> tuple[CredentialOutcome, str]:
    """Prompt for, validate against ``model``, and persist one LLM credential.

    Returns ``(outcome, model)``. For every non-Azure provider ``model`` is echoed back
    unchanged (the caller picked it up front). For Azure OpenAI the deployment name is a
    live value discovered from the resource, so it can only be chosen once the endpoint
    and key exist: the pick happens here, after both are collected but **before** the
    validation probe, so the returned ``model`` is still "the model the user picked,
    validated" — the caller persists whatever comes back (#4117 discovery + #3591 order).

    ``repick`` = pick a different LLM provider (mirrors ``_run_cli_llm_onboarding``).

    ``session_env_sink`` is forwarded to ``_persist_llm_credential_with_recovery`` so a
    ``continue_unsaved`` export is still in ``os.environ`` for the in-process shell
    handoff (#3591).
    """
    step(provider.credential_label.title())
    label = _credential_prompt_label(provider)
    credential_display = f"{label} {provider.credential_label}"
    env_key = provider.api_key_env
    is_azure = is_azure_openai_provider(provider.value)
    from core.llm.providers.custom_endpoints import is_custom_provider

    # Azure and the custom gateways both collect an endpoint before validation, so
    # a failed probe should re-prompt the endpoint too (a wrong base URL is the
    # usual cause). Only Azure additionally does live deployment discovery.
    needs_endpoint = is_azure or is_custom_provider(provider.value)
    for _attempt in range(_LLM_CREDENTIAL_MAX_ATTEMPTS):
        try:
            value = prompt_value(
                f"{credential_display} ({env_key}) — leave blank to set up later",
                # Only a ``host`` credential may be pre-filled. prompt_value returns the
                # default on empty input, and a secret provider's credential_default is a
                # placeholder (Azure's is an endpoint URL) — offering it would let a bare
                # Enter persist a URL as the API key.
                default=provider.credential_default
                if provider.credential_kind == WizardCredentialKind.HOST
                else "",
                secret=provider.credential_secret,
                # A blank answer is "I do not have this yet", not a mistake to
                # re-prompt: this was the only step that could end onboarding.
                allow_empty=provider.credential_kind != WizardCredentialKind.HOST,
                back_on_cancel=True,
            )
            if not value:
                return DEFERRED, model
        except WizardBack:  # must precede KeyboardInterrupt: WizardBack subclasses it
            return REPICK, model
        except KeyboardInterrupt:
            console.print(f"\n[{WARNING}]Setup cancelled.[/]")
            return CANCEL, model

        azure_env = ensure_provider_endpoint_settings(provider)
        if azure_env is None:  # endpoint back-out -> provider menu
            return REPICK, model
        os.environ.update(azure_env)  # endpoint env must exist before discovery/validation

        if is_azure:
            # Azure deployment discovery reads the key from the environment, so export it
            # before the picker calls the live resource. The deployment is chosen here —
            # after endpoint + key, before the validation probe — so the invariant
            # "validate the model the user picked" holds while discovery has its creds.
            os.environ[env_key] = value
            try:
                model = choose_provider_model(
                    provider,
                    default=model,
                    back_on_cancel=True,
                )
            except WizardBack:
                # Backing out of the deployment pick returns to the provider menu; drop the
                # endpoint so the re-selected Azure provider re-prompts it (#3591).
                _clear_provider_endpoint_env(provider)
                return REPICK, model

        validation = _validate_llm_credential(provider, value=value, model=model)
        _render_credential_validation(label, model, validation)
        if not validation.ok:
            action = _recovery_action(
                prompt=f"{credential_display} could not be verified. What next?",
                # Azure re-enters the endpoint too: a wrong resource URL, not the key, is
                # the usual cause of an Azure failure, and the endpoint is popped below so
                # it is genuinely re-prompted.
                retry_label=(
                    f"Re-enter the endpoint and {provider.credential_label}"
                    if needs_endpoint
                    else f"Re-enter the {provider.credential_label}"
                ),
                retry_hint=(
                    f"Prompts for {provider.endpoint_env} and {env_key} again"
                    if needs_endpoint
                    else f"Prompts for {env_key} again"
                ),
                escape=Choice(
                    value=SAVE_ANYWAY,
                    label="Save anyway without validating",
                    hint=(
                        f"Stores {env_key} unverified — use when you are offline, "
                        "proxied, or sure it is correct"
                    ),
                ),
            )
            if action == RETRY:
                # Drop the (possibly wrong) Azure endpoint so retry re-prompts it, not
                # just the key; a no-op for every non-Azure provider.
                _clear_provider_endpoint_env(provider)
                continue
            if action == REPICK:
                # Same reason as retry: the re-selected Azure provider must re-prompt the
                # endpoint instead of short-circuiting on the stale env var.
                _clear_provider_endpoint_env(provider)
                return REPICK, model
            if action != SAVE_ANYWAY:
                return CANCEL, model  # ESCAPE, or defensively: no other values are offered

        outcome = _persist_llm_credential_with_recovery(
            provider, value, session_env_sink=session_env_sink
        )
        if outcome != OK:
            return outcome, model
        if not validation.ok:
            # Printed only after the write succeeded, so it never claims a save that
            # did not happen.
            console.print(
                f"[{WARNING}]  {GLYPH_WARNING}  Saved {env_key} without validating it.[/]"
            )
            console.print(
                f"[{SECONDARY}]     If OpenSRE cannot reach {label}, "
                f"rerun `opensre onboard` and re-enter it.[/]"
            )
            return UNVERIFIED, model
        return OK, model
    console.print(f"[{WARNING}]  {GLYPH_WARNING}  Too many retry attempts. Aborting setup.[/]")
    return CANCEL, model
