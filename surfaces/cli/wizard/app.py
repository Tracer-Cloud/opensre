"""Run the OpenSRE quickstart wizard."""

from __future__ import annotations

import click

from config.local_env import bootstrap_opensre_env_once
from infrastructure.analytics.capture import capture_cli_invoked
from infrastructure.analytics.event_properties import build_cli_invoked_properties
from infrastructure.analytics.provider import capture_first_run_if_needed, shutdown_analytics
from infrastructure.observability.errors.sentry import init_sentry
from infrastructure.terminal.prompt_support import install_questionary_escape_cancel
from surfaces.cli.wizard.flow import run_wizard

_ENTRYPOINT = "python -m surfaces.cli.wizard"


def main() -> int:
    bootstrap_opensre_env_once(override=False)
    init_sentry(entrypoint="wizard")
    install_questionary_escape_cancel()

    capture_first_run_if_needed()
    capture_cli_invoked(
        build_cli_invoked_properties(
            entrypoint=_ENTRYPOINT,
            command_parts=["wizard"],
        ),
        ["onboard"],
    )

    try:
        return int(run_wizard())
    except KeyboardInterrupt:
        print(flush=True)
        return 0
    except click.Abort:
        print(flush=True)
        return 0
    finally:
        shutdown_analytics(flush=False)


if __name__ == "__main__":
    raise SystemExit(main())
