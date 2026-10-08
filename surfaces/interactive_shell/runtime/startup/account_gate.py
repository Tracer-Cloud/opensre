"""Webapp-account seams for the mandatory interactive-shell sign-in gate."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from config.constants import OPENSRE_PARENT_INTERACTIVE_SHELL_ENV
from infrastructure.analytics.source import is_test_run

if TYPE_CHECKING:
    from rich.console import Console

    from surfaces.shared.account_session import AccountStatus


def current_account_status() -> AccountStatus:
    """The local login as the webapp sees it now."""
    from surfaces.shared.account_session import account_status

    return account_status()


def account_is_signed_in() -> bool:
    """Return whether the webapp validates a complete local account session."""
    return current_account_status().authenticated


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


def pass_sign_in_gate(console: Console, *, on_screen: Callable[[], None] | None = None) -> bool:
    """Run the sign-in gate; return True to proceed into the REPL.

    Test processes skip the prompt (same reason as the loops picker) so pytest
    on a TTY cannot hang on the Sign in/Stay signed out choice. An app that
    cannot be reached ends the launch with that reason: the sign-in screen is
    only for a login that is missing or was rejected.
    ``on_screen`` fires once when the sign-in screen is actually painted, not
    when the user is already signed in or the gate fails closed without a menu.
    """
    if is_test_run():
        return True
    from infrastructure.analytics.capture import (
        capture_sign_in_prompted,
        capture_sign_in_selected,
        capture_stay_signed_out_selected,
    )
    from infrastructure.terminal.theme import ERROR
    from surfaces.interactive_shell.ui.sign_in import SignInChoice, run_sign_in_gate
    from surfaces.shared.account_session import AccountSessionState

    status = current_account_status()
    if status.state is AccountSessionState.UNAVAILABLE:
        # The app did not answer, so the saved login is neither confirmed nor
        # rejected. Signing in again cannot help; say what happened instead.
        console.print(f"[{ERROR}]{status.detail}[/]")
        console.print("Your saved login was kept. Run [bold]opensre[/bold] again in a minute.")
        return False

    def _login() -> bool:
        return account_login(console=console)

    def _record_choice(choice: SignInChoice | None) -> None:
        # Recorded before login runs so the intent survives a failed or
        # abandoned browser flow. ``None`` is any dismissal (Esc, q, Ctrl-C,
        # Ctrl-D, EOF); the picker does not distinguish them, so neither do we.
        if choice is SignInChoice.LOGIN:
            capture_sign_in_selected(choice_label=choice.value)
            return
        capture_stay_signed_out_selected(
            choice_label=SignInChoice.EXIT.value,
            method="menu" if choice is SignInChoice.EXIT else "dismissed",
        )

    def _on_prompted() -> None:
        capture_sign_in_prompted()
        if on_screen is not None:
            on_screen()

    return run_sign_in_gate(
        console,
        is_signed_in=lambda: status.authenticated,
        login=_login,
        on_prompted=_on_prompted,
        on_choice=_record_choice,
    )


__all__ = [
    "account_is_signed_in",
    "current_account_status",
    "account_login",
    "pass_sign_in_gate",
]
