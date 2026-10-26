"""Shared UI components for the wizard onboarding flow.

This is the wizard's interaction layer: the ``Choice`` type and ``WizardBack``
control-flow exception, the shared ``console``, step headers, and the
choice/value/confirm/model prompts that compose wizard steps. It sits above
:mod:`surfaces.cli.wizard.prompts`, which builds the raw prompt-toolkit
select/checkbox widgets — ``prompts`` renders a list control, this module
orchestrates it into a wizard step. Rendered output screens (splash, saved
summary, integration result, next steps) live in
:mod:`surfaces.cli.wizard.summaries`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

import questionary
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from config.llm_auth.credentials import has_llm_api_key, save_api_key
from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS
from config.llm_credentials import get_keyring_setup_instructions, save_credential
from config.setup_store import get_store_path, load_local_config
from infrastructure.terminal.theme import (
    BG,
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_WARNING,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from integrations.store import get_integration
from surfaces.cli.wizard.prompts import select as select_prompt
from surfaces.shared.llm_setup.catalog import (
    PROVIDER_BY_VALUE,
    ProviderOption,
    WizardCredentialKind,
)
from surfaces.shared.llm_setup.persist import AuthSetupError, persist_api_key_secret

console = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)


def _questionary_style() -> questionary.Style:
    """Build questionary styles from the active terminal theme.

    Highlighted list rows use ``BG`` (dark) on ``HIGHLIGHT`` (light accent) so
    selected options stay readable across every palette — light ``TEXT`` on a
    light ``HIGHLIGHT`` background was nearly invisible in green and similar themes.
    """
    return questionary.Style(
        [
            ("qmark", f"fg:{HIGHLIGHT} bold"),
            ("question", f"fg:{TEXT} bold"),
            ("answer", f"fg:{BRAND} bold"),
            ("pointer", f"fg:{HIGHLIGHT} bold"),
            ("highlighted", f"fg:{BG} bg:{HIGHLIGHT} bold"),
            ("selected", f"fg:{TEXT} bg:default bold"),
            ("group-header", f"fg:{HIGHLIGHT} bold"),
            ("separator", f"fg:{DIM}"),
            ("text", f"fg:{TEXT} bg:default"),
            ("disabled", f"fg:{SECONDARY} bg:default italic"),
            ("instruction", f"fg:{SECONDARY} italic"),
        ]
    )


def group_header_label(group: str) -> str:
    """Format a category label for grouped wizard pickers."""
    return f"── {group} ──"


@dataclass(frozen=True)
class Choice:
    """A selectable wizard choice."""

    value: str
    label: str
    group: str | None = None
    hint: str | None = None


class WizardBack(KeyboardInterrupt):
    """Raised when a prompt-level cancel should move back one wizard step."""


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def string_value(value: object, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def joined_values(value: object, *, separator: str, fallback: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return separator.join(value)
    return fallback


def local_defaults() -> dict[str, str | bool | None]:
    stored = load_local_config(get_store_path())
    wizard = _as_mapping(stored.get("wizard"))
    targets = _as_mapping(stored.get("targets"))
    local = _as_mapping(targets.get("local"))
    raw_provider = local.get("provider")
    provider_value = string_value(raw_provider) if raw_provider else ""
    provider = PROVIDER_BY_VALUE.get(provider_value) if provider_value else None
    api_key_env = string_value(local.get("api_key_env"), provider.api_key_env if provider else "")
    is_cli = bool(provider and provider.credential_kind == WizardCredentialKind.CLI)
    is_host = bool(provider and provider.credential_kind == WizardCredentialKind.HOST)
    wizard_mode = string_value(wizard.get("mode"), "quickstart")
    if wizard_mode in {"aha", "focused"}:
        wizard_mode = "quickstart"
    return {
        "wizard_mode": wizard_mode,
        "provider": provider_value or None,
        "model": string_value(local.get("model")),
        "api_key_env": api_key_env,
        # A ``host`` credential (e.g. the Ollama host) is only real when the
        # runtime can see it — the environment — never the keyring.
        "has_api_key": True
        if is_cli
        else (
            bool(api_key_env and os.getenv(api_key_env, "").strip())
            if is_host
            else bool(api_key_env and has_llm_api_key(api_key_env))
        ),
        "legacy_api_key": string_value(local.get("api_key")),
    }


def integration_defaults(service: str) -> tuple[Mapping[str, object], Mapping[str, object]]:
    entry = _as_mapping(get_integration(service))
    return entry, _as_mapping(entry.get("credentials"))


def step(title: str) -> None:
    console.print()
    t = Text()
    t.append("  ")
    t.append(title, style=f"bold {HIGHLIGHT}")
    console.print(t)
    console.print(Rule(style=DIM))


def step_header(n: int, total: int, title: str) -> None:
    """Print a numbered wizard stage header.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
      ●●○○  LLM Provider  2/4                   [BRAND dots] [TEXT title] [SECONDARY counter]
      ─────────────────────────────────────────  [DIM rule]
    """
    dots = "●" * n + "○" * (total - n)
    console.print()
    console.print(Rule(style=DIM))
    header = Text()
    header.append("  ")
    header.append(dots, style=f"bold {BRAND}")
    header.append("  ", style=DIM)
    header.append(title, style=f"bold {TEXT}")
    header.append(f"  {n}/{total}", style=SECONDARY)
    console.print(header)
    console.print(Rule(style=DIM))


def _choice_title(choice: Choice) -> str:
    return choice.label


def _choice_description(choice: Choice) -> str | None:
    if choice.hint:
        return choice.hint
    return choice.group


def _questionary_choice(choice: Choice) -> questionary.Choice:
    return questionary.Choice(
        title=_choice_title(choice),
        value=choice.value,
        description=_choice_description(choice),
    )


def grouped_questionary_choices(
    choices: list[Choice],
    *,
    group_order: tuple[str, ...],
    trailing_choices: list[Choice] | None = None,
) -> list[questionary.Choice | questionary.Separator]:
    """Render selectable choices with non-selectable category separators."""
    grouped: dict[str, list[Choice]] = {group: [] for group in group_order}
    ungrouped: list[Choice] = []

    for choice in choices:
        if choice.group is None or choice.group not in grouped:
            ungrouped.append(choice)
            continue
        grouped[choice.group].append(choice)

    rendered: list[questionary.Choice | questionary.Separator] = []
    for group in group_order:
        group_choices = grouped[group]
        if not group_choices:
            continue
        rendered.append(questionary.Separator(group_header_label(group)))
        rendered.extend(_questionary_choice(choice) for choice in group_choices)

    if ungrouped:
        rendered.append(questionary.Separator(group_header_label("Other")))
        rendered.extend(_questionary_choice(choice) for choice in ungrouped)

    if trailing_choices:
        rendered.append(questionary.Separator())
        rendered.extend(_questionary_choice(choice) for choice in trailing_choices)

    return rendered


CUSTOM_MODEL_SENTINEL = "__custom__"


def _provider_model_prompt_label(provider: ProviderOption) -> str:
    """Provider label without auth-method suffixes that read badly in model prompts."""
    for suffix in (" API key", " OAuth"):
        if provider.label.endswith(suffix):
            return provider.label[: -len(suffix)]
    return provider.label


def choose_model(
    provider: ProviderOption,
    *,
    default: str | None,
    prompt_label: str | None = None,
    back_on_cancel: bool = False,
) -> str:
    """Prompt the user to pick a model from ``provider.models``.

    Choices come from the curated config in ``surfaces/shared/llm_setup/catalog.py``.
    A saved model that isn't in the curated list is preserved as ``current``
    so re-running the wizard never silently drops a user's prior pick, and an
    "Enter custom model ID" escape hatch is always available.
    """
    resolved_default = (default or "").strip()
    models = provider.models
    if not models:
        # Providers with no curated catalog but arbitrary model IDs (custom
        # OpenAI-/Anthropic-compatible gateways) must still be asked for a model
        # ID rather than silently returning an empty default.
        if provider.allow_custom_models:
            step("Model")
            return prompt_value(
                f"{_provider_model_prompt_label(provider)} model ID ({provider.model_env})",
                default=resolved_default or provider.default_model,
                allow_empty=False,
                back_on_cancel=back_on_cancel,
            )
        return resolved_default or provider.default_model

    step("Model")

    curated_values = {option.value for option in models}
    curated_choices: list[Choice] = [
        Choice(value=option.value, label=option.label) for option in models
    ]

    extra_choices: list[Choice] = []
    if resolved_default and resolved_default not in curated_values:
        extra_choices.append(Choice(value=resolved_default, label=resolved_default, hint="current"))

    custom_choice = Choice(
        value=CUSTOM_MODEL_SENTINEL,
        label="Enter custom model ID",
        hint="type any model identifier",
    )

    choices = curated_choices + extra_choices + [custom_choice]
    default_value = resolved_default or provider.default_model
    if default_value and not any(c.value == default_value for c in choices):
        default_value = curated_choices[0].value if curated_choices else CUSTOM_MODEL_SENTINEL

    provider_label = prompt_label or _provider_model_prompt_label(provider)
    selection = choose(
        f"Choose {provider_label} model",
        choices,
        default=default_value or None,
        back_on_cancel=back_on_cancel,
    )

    if selection != CUSTOM_MODEL_SENTINEL:
        return selection

    return prompt_value(
        f"Custom {provider_label} model ID ({provider.model_env})",
        default=resolved_default,
        allow_empty=False,
        back_on_cancel=back_on_cancel,
    )


def choose(
    prompt: str,
    choices: list[Choice],
    *,
    default: str | None = None,
    group_order: tuple[str, ...] | None = None,
    trailing_choices: list[Choice] | None = None,
    back_on_cancel: bool = False,
) -> str:
    if group_order is not None:
        q_choices = grouped_questionary_choices(
            choices,
            group_order=group_order,
            trailing_choices=trailing_choices,
        )
    else:
        q_choices = [_questionary_choice(choice) for choice in choices]
        if trailing_choices:
            q_choices.append(questionary.Separator())
            q_choices.extend(_questionary_choice(choice) for choice in trailing_choices)

    result = select_prompt(
        prompt,
        choices=q_choices,
        default=default,
        style=_questionary_style(),
        instruction="(Use arrows to move, Enter to choose)",
    ).ask()

    if result is None:
        if back_on_cancel:
            raise WizardBack
        raise KeyboardInterrupt
    return str(result)


def confirm(prompt: str, *, default: bool = True) -> bool:
    result = questionary.confirm(prompt, default=default, style=_questionary_style()).ask()
    if result is None:
        raise KeyboardInterrupt
    return bool(result)


def prompt_value(
    label: str,
    *,
    default: str = "",
    secret: bool = False,
    allow_empty: bool = False,
    back_on_cancel: bool = False,
) -> str:
    while True:
        instruction = "(Enter to keep current)" if default else None
        if secret:
            result = questionary.password(
                label,
                default=default,
                style=_questionary_style(),
                instruction=instruction,
            ).ask()
        else:
            result = questionary.text(
                label,
                default=default,
                style=_questionary_style(),
                instruction=instruction,
            ).ask()

        if result is None:
            if back_on_cancel:
                raise WizardBack
            raise KeyboardInterrupt

        value = str(result).strip()
        if value:
            return value
        if default:
            return default
        if allow_empty:
            return ""
        console.print(f"[{ERROR}]  {GLYPH_ERROR}  Required.[/]")


def _write_llm_api_key_to_env(env_var: str, value: str) -> None:
    """Mirror a saved API key into the project ``.env``."""
    from config.env_file import sync_env_values

    sync_env_values({env_var: value})


def persist_llm_api_key(env_var: str, value: str) -> bool:
    try:
        provider = next(
            (
                name
                for name, provider_env in API_KEY_PROVIDER_ENVS.items()
                if provider_env == env_var
            ),
            "",
        )
        if provider:
            result = save_api_key(provider, value)
        else:
            result = persist_api_key_secret(env_var, value, save_secret=save_credential)
        detail = result.detail if result.used_fallback else ""
        _write_llm_api_key_to_env(env_var, value)
    except (AuthSetupError, RuntimeError, ValueError, OSError) as exc:
        console.print(f"[{ERROR}]  {GLYPH_ERROR}  {exc}[/]")
        console.print(
            f"[{WARNING}]  {GLYPH_WARNING}  OpenSRE could not save your API key to secure local storage.[/]"
        )
        for line in get_keyring_setup_instructions(env_var):
            console.print(f"[{SECONDARY}]    {line}[/]")
        return False
    if detail:
        # Saved, but not where we would have preferred — say so rather than
        # letting a silent downgrade look like a successful write.
        console.print(f"[{WARNING}]  {GLYPH_WARNING}  {detail}[/]")
    return True
