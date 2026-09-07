"""Webapp-account seams for the mandatory interactive-shell sign-in gate."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from config.constants import OPENSRE_PARENT_INTERACTIVE_SHELL_ENV
from infrastructure.analytics.source import is_test_run

if TYPE_CHECKING:
    from rich.console import Console


def account_is_signed_in() -> bool:
    """Return whether the webapp validates a complete local account session."""
    from surfaces.shared.account_session import account_status

    return account_status().authenticated


def account_login(*, console: Console | None = None) -> bool:
    """Run the canonical webapp login command and verify its resulting session."""
    from infrastructure.terminal.theme import ERROR
    from surfaces.interactive_shell.runtime.subprocess_runner import build_opensre_cli_argv

    command = build_opensre_cli_argv(["account", "login"])
    try:
        result = subprocess.run(
            command,
            check=False,
            env={**os.environ, OPENSRE_PARENT_INTERACTIVE_SHELL_ENV: "1"},
        )
    except Exception:
        if console is not None:
            console.print(f"[{ERROR}]OpenSRE account sign-in could not start.[/]")
        return False
    if result.returncode != 0:
        if console is not None:
            console.print(f"[{ERROR}]OpenSRE account sign-in did not complete.[/]")
        return False
    return account_is_signed_in()


def pass_sign_in_gate(console: Console) -> bool:
    """Run the sign-in gate; return True to proceed into the REPL.

    Test processes skip the prompt (same reason as the loops picker) so pytest
    on a TTY cannot hang on the Sign in/Stay signed out choice.
    """
    if is_test_run():
        return True
    from surfaces.interactive_shell.ui.sign_in import run_sign_in_gate

    def _login() -> bool:
        return account_login(console=console)

    return run_sign_in_gate(
        console,
        is_signed_in=account_is_signed_in,
        login=_login,
    )


__all__ = [
    "account_is_signed_in",
    "account_login",
    "pass_sign_in_gate",
]
