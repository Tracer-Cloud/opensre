"""One shared provider/source picker for login and model setup."""

from __future__ import annotations

import config.llm_settings as llm_settings
from surfaces.shared.llm_setup.auth_profiles import (
    ProviderAuthProfile,
    iter_auth_profiles,
    resolve_auth_profile,
)
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
from surfaces.shared.llm_setup.provider_sources import source_label
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import InputChoice, InputField, build_input_form


def configured_profile_name() -> str | None:
    provider = llm_settings.get_configured_llm_provider()
    return next((p.name for p in iter_auth_profiles() if p.provider_value == provider), None)


def prompt_auth_provider() -> ProviderAuthProfile | None:
    profiles = sorted(
        iter_auth_profiles(),
        key=lambda profile: PROVIDER_BY_VALUE[profile.provider_value].label.casefold(),
    )
    choices = tuple(
        InputChoice(
            p.name, source_label(PROVIDER_BY_VALUE[p.provider_value]), details=(p.auth_hint,)
        )
        for p in profiles
    )
    app = build_input_form(
        {"provider": configured_profile_name() or choices[0].value or ""},
        title="Connect a provider",
        heading="Choose a provider and source",
        submit_label="Connect",
        fields=lambda _: (
            InputField(
                "provider",
                "Provider / source",
                required=True,
                choices=choices,
                searchable=True,
                record_choices=True,
            ),
        ),
        validate=lambda values: values["provider"],
        initial_field="provider",
        submit_on_choice=True,
    )
    selected = run_command_input(app)
    return resolve_auth_profile(selected) if selected else None
