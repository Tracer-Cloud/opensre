"""The onboarding wizard's configurator for any spec-driven integration.

This is the *collection* half of setup, and the counterpart to
:mod:`integrations.setup_flow`: that module takes values someone already
gathered and decides where they are persisted, while this one is what asks the
user for them.

Every configurator built on an :class:`~integrations.setup_flow.IntegrationSetupSpec`
does the same three things — prompt for each field (prefilled from whatever is
already stored, so re-running onboarding is not a retype), hand the answers to
:func:`~integrations.setup_flow.apply_setup`, and re-ask on failure instead of
dropping the user out of the wizard. Only the heading and the introductory
guidance differ, so those are the arguments.
"""

from __future__ import annotations

from collections.abc import Callable

from infrastructure.terminal.theme import SECONDARY
from integrations.setup_flow import IntegrationSetupSpec, apply_setup
from surfaces.cli.wizard.components import (
    Choice,
    choose,
    console,
    integration_defaults,
    joined_values,
    prompt_value,
    string_value,
)
from surfaces.cli.wizard.integration_validators.shared import IntegrationHealthResult
from surfaces.cli.wizard.summaries import render_integration_result

#: Vendor hook run after the mode picker and before its fields are prompted.
#: Returns the mode to continue with — the same one, or a different one when
#: the vendor steered the user (a prerequisite the chosen mode needs is absent).
ModeGate = Callable[[str], str]


def configure_from_spec(
    spec: IntegrationSetupSpec,
    *,
    title: str,
    intro: str = "",
    on_mode_chosen: ModeGate | None = None,
) -> tuple[str, str]:
    """Prompt for *spec*'s fields until they verify, then persist them.

    *on_mode_chosen* lets a vendor check a mode's prerequisites at the moment
    it is picked — before the user types credentials that cannot work — and
    steer to another mode. Returns the pair the wizard's configurator table
    expects: the display name and the ``.env`` path that was written.
    """
    if spec.guide is not None:
        from integrations.setup import run_guided_setup

        outcome = run_guided_setup(spec)
        assert outcome.env_path is not None
        return title, str(outcome.env_path)

    _, credentials = integration_defaults(spec.service)
    if intro:
        console.print(intro)
    while True:
        mode: str | None = None
        if spec.mode_prompt:
            mode = choose(
                spec.mode_prompt,
                [Choice(value=m.value, label=m.label) for m in spec.modes],
                default=spec.modes[0].value,
            )
            if on_mode_chosen is not None:
                mode = on_mode_chosen(mode)
        collectable = {field.name for field in spec.collectable_fields(mode)}
        values: dict[str, str | None] = {}
        for field in spec.fields:
            if field.is_constant:
                values[field.name] = field.constant
                continue
            if field.name not in collectable:
                # Gated field for an unchosen mode: clear it rather than prompt,
                # so switching modes turns the other mode's credentials off.
                values[field.name] = ""
                continue
            stored = credentials.get(field.name)
            # Prefer a joined list when the store still has a sequence (e.g. Better
            # Stack ``sources`` from the pre-spec wizard). ``string_value`` alone
            # would drop the list and prefill blank, so Enter would clear it.
            default = joined_values(
                stored, separator=",", fallback=string_value(stored, field.default)
            )
            while True:
                answer = prompt_value(
                    field.question,
                    # A stored value wins over the spec's default, so re-running
                    # onboarding is a series of enters rather than a retype.
                    default=default,
                    secret=field.secret,
                    # Only reached when the field has no default to fall back on:
                    # prompt_value substitutes the default before it consults this,
                    # so a defaulted field never re-prompts and never returns blank.
                    allow_empty=not spec.is_required(field, mode),
                )
                problem = field.validate(answer) if answer and field.validate else None
                if problem is None:
                    break
                console.print(f"[{SECONDARY}]{problem}[/]")
            values[field.name] = answer
        with console.status(f"Validating {title} credentials...", spinner="dots"):
            outcome = apply_setup(spec, values)
        render_integration_result(
            title, IntegrationHealthResult(ok=outcome.ok, detail=outcome.detail)
        )
        if outcome.ok:
            # apply_setup always resolves an .env path on success; narrow for mypy
            # and fail loudly rather than returning the string "None" if it ever
            # stops doing so.
            assert outcome.env_path is not None, "apply_setup returned ok=True without an env_path"
            return title, str(outcome.env_path)
        console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
