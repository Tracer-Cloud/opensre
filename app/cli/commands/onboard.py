"""Onboarding-related CLI commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from app.analytics.cli import (
    capture_onboard_completed,
    capture_onboard_failed,
    capture_onboard_started,
)

ConfigLoader = Callable[[], dict[str, Any]]
RunCommand = Callable[[], int]


def _load_local_config() -> dict[str, Any]:
    from app.cli.wizard.store import get_store_path, load_local_config

    return load_local_config(get_store_path())


def _run_onboarding_command(
    run_command: RunCommand, *, load_config: ConfigLoader = _load_local_config
) -> None:
    capture_onboard_started()
    try:
        exit_code = run_command()
    except Exception:
        capture_onboard_failed()
        raise

    if exit_code == 0:
        capture_onboard_completed(load_config())
    else:
        capture_onboard_failed()
    raise SystemExit(exit_code)


@click.group(name="onboard", invoke_without_command=True)
@click.pass_context
def onboard(ctx: click.Context) -> None:
    """Run the interactive onboarding wizard."""
    if ctx.invoked_subcommand is not None:
        return

    from app.cli.wizard import run_wizard

    _run_onboarding_command(run_wizard)


@onboard.command(name="local_llm")
def onboard_local_llm() -> None:
    """Zero-config local LLM setup via Ollama. No API key required."""
    from app.cli.local_llm.command import run_local_llm_setup

    _run_onboarding_command(run_local_llm_setup)
