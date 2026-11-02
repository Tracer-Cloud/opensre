"""GitHub seams for the mandatory interactive-shell connection gate.

"Connected" is the skills' GitHub prerequisite (``GITHUB_REST_TOKEN_CHECK``):
an authorized GitHub connection made in the OpenSRE app, the workspace's or
the member's own — the same rule the app enforces on every hosted model call.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from config.constants.skill_prerequisites import GITHUB_REST_TOKEN_CHECK
from infrastructure.analytics.source import is_test_run
from infrastructure.harness_providers import resolve_integrations, skill_prerequisite_verdict
from infrastructure.terminal.browser import open_in_browser
from infrastructure.terminal.theme import DIM
from integrations.account_integrations import load_account_integrations
from integrations.github import github_setup_url


def github_connected(*, refresh: bool = False) -> bool:
    """Whether this login sees a connected GitHub account; ``refresh`` asks the app again.

    Only the registered check's positive answer counts: a check that cannot
    tell keeps the shell closed, since the app would refuse the turn anyway.
    """
    if refresh:
        load_account_integrations(refresh=True)
    return skill_prerequisite_verdict(GITHUB_REST_TOKEN_CHECK, resolve_integrations()) is True


def open_github_dashboard(console: Console, url: str) -> None:
    """Open the dashboard's Connect GitHub step in the browser; print the link either way.

    Over SSH or on a server nothing opens: the link works in a browser on any
    device, since GitHub returns to the app, not to this machine.
    """
    opened = open_in_browser(url, target="integration_setup")
    lead = f"Opened {escape(url)}" if opened else f"Open {escape(url)} in a browser on any device"
    console.print(f"[{DIM}]{lead} and connect GitHub there.[/]", soft_wrap=True)


def pass_github_gate(console: Console, *, before_screen: Callable[[], None] | None = None) -> bool:
    """Run the GitHub gate after sign-in; return True to proceed into the REPL.

    Test processes skip it, as they skip the sign-in gate. ``before_screen``
    runs once, only when the screen is about to paint (to stop the launch spin).
    """
    if is_test_run():
        return True
    from surfaces.interactive_shell.ui.connect_github import run_connect_github_gate
    from surfaces.shared.terminal.components.rendering import repl_clear_screen

    url = github_setup_url()

    def _before_screen() -> None:
        if before_screen is not None:
            before_screen()
        repl_clear_screen()

    return run_connect_github_gate(
        console,
        is_connected=github_connected,
        recheck=lambda: github_connected(refresh=True),
        open_dashboard=lambda: open_github_dashboard(console, url),
        dashboard_url=url,
        on_prompted=_before_screen,
    )


__all__ = ["github_connected", "open_github_dashboard", "pass_github_gate"]
