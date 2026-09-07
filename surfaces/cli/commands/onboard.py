"""Onboarding-related CLI commands."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

import click

from config.constants import OPENSRE_PARENT_INTERACTIVE_SHELL_ENV
from infrastructure.analytics.capture import (
    capture_onboard_completed,
    capture_onboard_failed,
    capture_onboard_started,
)

ConfigLoader = Callable[[], dict[str, Any]]
RunCommand = Callable[[], int]

OPENSRE_AUTO_LAUNCH_ENV = "OPENSRE_AUTO_LAUNCH"
_DISABLED_ENV_VALUES = {"0", "false", "no", "off"}


def _load_local_config() -> dict[str, Any]:
    from config.setup_store import get_store_path, load_local_config

    return load_local_config(get_store_path())


def _run_onboarding_command(
    run_command: RunCommand,
    *,
    ctx: click.Context | None = None,
    load_config: ConfigLoader = _load_local_config,
) -> None:
    from surfaces.shared.error_handling.errors import OpenSREError

    capture_onboard_started()
    try:
        exit_code = run_command()
    except PermissionError as exc:
        capture_onboard_failed()
        raise OpenSREError(
            str(exc),
            suggestion="Check file permissions or set OPENSRE_PROJECT_ENV_PATH to a writable path.",
        ) from exc
    except Exception:
        capture_onboard_failed()
        raise

    if exit_code == 0:
        capture_onboard_completed(load_config())
        if _should_launch_shell_after_onboarding(ctx):
            exit_code = _launch_interactive_shell(ctx)
    else:
        capture_onboard_failed()
    raise SystemExit(exit_code)


def _env_auto_launch_disabled() -> bool:
    return os.getenv(OPENSRE_AUTO_LAUNCH_ENV, "").strip().lower() in _DISABLED_ENV_VALUES


def _launched_from_interactive_shell() -> bool:
    return bool(os.getenv(OPENSRE_PARENT_INTERACTIVE_SHELL_ENV, "").strip())


def _click_interactive_enabled(ctx: click.Context | None) -> bool:
    if ctx is None:
        return True
    root = ctx.find_root()
    obj = root.obj if isinstance(root.obj, dict) else {}
    return bool(obj.get("interactive", True))


def _should_launch_shell_after_onboarding(ctx: click.Context | None) -> bool:
    if _env_auto_launch_disabled() or _launched_from_interactive_shell():
        return False
    if not _click_interactive_enabled(ctx):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _launch_interactive_shell(ctx: click.Context | None) -> int:
    from config.repl_config import ReplConfig
    from surfaces.cli.host import cli_host

    launch_shell = cli_host(ctx).launch_shell if ctx is not None else None
    if launch_shell is None:
        return 0
    # A subcommand initialised error reporting in line: nothing is held back.
    return launch_shell(ReplConfig.load(cli_enabled=True), None, None)


@click.group(name="onboard", invoke_without_command=True)
@click.pass_context
def onboard(ctx: click.Context) -> None:
    """Run the interactive onboarding wizard."""
    if ctx.invoked_subcommand is not None:
        return

    from surfaces.cli.wizard.flow import run_wizard

    _run_onboarding_command(run_wizard, ctx=ctx)


@onboard.command(name="local_llm")
@click.pass_context
def onboard_local_llm(ctx: click.Context) -> None:
    """Zero-config local LLM setup via Ollama. No API key required."""
    from surfaces.cli.wizard.local_llm.command import run_local_llm_setup

    _run_onboarding_command(run_local_llm_setup, ctx=ctx)
