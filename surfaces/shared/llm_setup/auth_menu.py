"""Provider connection picker shared by CLI and REPL login."""

from __future__ import annotations

from rich.text import Text

from surfaces.shared.llm_setup.auth_profiles import (
    ProviderAuthProfile,
    iter_auth_profiles,
)
from surfaces.shared.llm_setup.connection_summary import summarize_connection
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow


def configured_profile_name() -> str | None:
    """The auth-profile name matching the install's active LLM provider, if any."""
    from config.llm_settings import get_configured_llm_provider

    configured = get_configured_llm_provider()
    for profile in iter_auth_profiles():
        if configured == profile.provider_value or configured in profile.all_names:
            return profile.name
    return None


def prompt_auth_provider() -> ProviderAuthProfile | None:
    """Choose from every supported route in one searchable, non-probing catalog."""
    profiles = tuple(sorted(iter_auth_profiles(), key=lambda profile: profile.label.casefold()))
    if not profiles:
        return None

    by_name = {profile.name: profile for profile in profiles}
    choices: list[InputChoice] = []
    for profile in profiles:
        summary = summarize_connection(profile)
        choices.append(
            InputChoice(
                value=profile.name,
                label=profile.label,
                summary=f"{summary.route} · {summary.billing}",
                summary_style="description",
                details=summary.detail_lines,
                search_text=" ".join(
                    (
                        profile.name,
                        profile.provider_value,
                        profile.label,
                        summary.route,
                        summary.account,
                        summary.authentication,
                        summary.execution,
                        summary.billing,
                    )
                ),
                record=RecordRow(
                    cells=(
                        Text(summary.provider),
                        Text(summary.route),
                        Text(summary.billing),
                    ),
                    details=tuple(Text(line) for line in summary.detail_lines),
                ),
            )
        )

    configured = configured_profile_name()
    initial = configured if configured in by_name else profiles[0].name
    columns = tuple(RecordColumn(name) for name in ("Provider", "Route", "Billing"))

    def fields(_draft: dict[str, str]) -> list[InputField]:
        return [
            InputField(
                "provider",
                "Connection",
                description=(
                    "Search by provider or route. Each entry is a distinct authentication "
                    "and execution path; billing is shown separately."
                ),
                required=True,
                choices=tuple(choices),
                searchable=True,
                record_choices=True,
                record_columns=columns,
            )
        ]

    def validate(draft: dict[str, str]) -> str:
        selected = draft["provider"]
        if selected not in by_name:
            raise InputFormError("Choose a listed connection route.", field="provider")
        return selected

    app = build_input_form(
        {"provider": initial},
        title="Connect a provider",
        heading="Available connections",
        submit_label="Continue",
        fields=fields,
        validate=validate,
        initial_field="provider",
        submit_on_choice=True,
    )
    selected = run_command_input(app)
    return by_name.get(selected) if selected is not None else None
