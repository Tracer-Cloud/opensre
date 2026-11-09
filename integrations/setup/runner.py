"""Run a vendor's human hand-offs and retry verification before saving."""

from __future__ import annotations

from integrations.setup.guidance import TerminalSetupUI
from integrations.setup_flow import IntegrationSetupSpec, SetupOutcome, SetupUI, apply_setup
from integrations.store import get_integration


def run_guided_setup(spec: IntegrationSetupSpec, *, ui: SetupUI | None = None) -> SetupOutcome:
    """Keep accepted answers in memory until verification and persistence succeed."""
    assert spec.guide is not None
    ui = ui or TerminalSetupUI()
    stored = (get_integration(spec.service) or {}).get("credentials") or {}
    values = {name: str(value) for name, value in stored.items() if value is not None}
    ui.say("I'll guide you through three steps. Press Ctrl+C at any time to cancel.")
    values = spec.guide(ui, values)
    while True:
        ui.step("three", "Verify and save")
        ui.say("Checking your connection and selected destination…")
        outcome = apply_setup(spec, values)
        if outcome.ok:
            ui.say(f"Verified {spec.service}. {outcome.detail}")
            ui.say(f"Saved. Check again any time with /integrations verify {spec.service}.")
            return outcome
        # Verifiers may include provider response bodies. Never echo a submitted secret.
        detail = outcome.detail
        for field in spec.fields:
            if field.secret and values.get(field.name):
                detail = detail.replace(values[field.name], "[redacted]")
        ui.say(detail)
        action = ui.choose(
            "Let's fix this here",
            [("retry", "Retry the check"), ("edit", "Change my answers"), ("cancel", "Cancel")],
        )
        if action == "edit":
            values = spec.guide(ui, values)
