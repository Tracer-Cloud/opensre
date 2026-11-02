"""GitHub seams for the mandatory interactive-shell connection gate.

"Connected" is the skills' GitHub prerequisite (``GITHUB_REST_TOKEN_CHECK``):
an authorized GitHub connection made in the OpenSRE app, the workspace's or
the member's own — the same rule the app enforces on every hosted model call.
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable, Mapping
from typing import Any

from rich.console import Console
from rich.markup import escape

from config.constants.skill_prerequisites import GITHUB_REST_TOKEN_CHECK
from infrastructure.analytics.capture import (
    capture_browser_open_requested,
    capture_github_gate_exited,
    capture_github_gate_prompted,
    capture_github_gate_rechecked,
)
from infrastructure.analytics.source import is_test_run
from infrastructure.harness_providers import resolve_integrations, skill_prerequisite_verdict
from infrastructure.terminal.theme import DIM
from integrations.account_integrations import load_account_integrations
from integrations.github import github_setup_url

#: Selection failures ``select_github_connection`` records; anything else is reported generically.
_SELECTION_ERRORS = frozenset({"github_connection_required", "github_connection_unavailable"})


def github_gate_check(*, refresh: bool = False) -> tuple[bool, str | None]:
    """Whether this login sees a connected GitHub account, and a reason code when it does not.

    Only the registered check's positive answer counts: a check that cannot
    tell keeps the shell closed, since the app would refuse the turn anyway.
    ``refresh`` asks the app again. The reason is a fixed code, never a token
    or an account name: ``check_unavailable`` (the check could not answer),
    ``no_github_connection`` (no GitHub connection reached this login),
    a selection failure (``github_connection_required`` or
    ``github_connection_unavailable``), or ``token_missing`` (a GitHub entry
    without an app-issued token).
    """
    if refresh:
        load_account_integrations(refresh=True)
    resolved = resolve_integrations()
    verdict = skill_prerequisite_verdict(GITHUB_REST_TOKEN_CHECK, resolved)
    if verdict is True:
        return True, None
    if verdict is None:
        return False, "check_unavailable"
    return False, _not_connected_reason(resolved)


def _not_connected_reason(resolved: Mapping[str, Any]) -> str:
    # Only a closed gate asks why, so the tool package stays off the launch path.
    from core.tool import availability_view

    github = availability_view(dict(resolved)).get("github")
    if not isinstance(github, Mapping) or not github:
        return "no_github_connection"
    error = github.get("connection_selection_error")
    if error:
        return (
            error if isinstance(error, str) and error in _SELECTION_ERRORS else "selection_failed"
        )
    return "token_missing"


def github_connected(*, refresh: bool = False) -> bool:
    """Whether this login sees a connected GitHub account; ``refresh`` asks the app again."""
    return github_gate_check(refresh=refresh)[0]


def open_github_dashboard(console: Console, url: str) -> None:
    """Open the dashboard's Connect GitHub step in the browser; print the link either way."""
    opened = False
    try:
        opened = bool(webbrowser.open(url))
    except (webbrowser.Error, OSError):
        opened = False
    finally:
        capture_browser_open_requested(target="integration_setup", opened=opened)
    lead = "Opened" if opened else "Open"
    console.print(f"[{DIM}]{lead} {escape(url)} and connect GitHub there.[/]")


def pass_github_gate(console: Console, *, before_screen: Callable[[], None] | None = None) -> bool:
    """Run the GitHub gate after sign-in; return True to proceed into the REPL.

    Test processes skip it, as they skip the sign-in gate. ``before_screen``
    runs once, only when the screen is about to paint (to stop the launch spin).
    A painted screen records its exposure, every recheck with the app's answer,
    and leaving without a connection; a connected member and a non-interactive
    run record nothing.
    """
    if is_test_run():
        return True
    from surfaces.interactive_shell.ui.connect_github import (
        ConnectGithubChoice,
        run_connect_github_gate,
    )
    from surfaces.shared.terminal.components.rendering import repl_clear_screen

    url = github_setup_url()
    launch_reason: str | None = None
    rechecks = 0

    def _is_connected() -> bool:
        nonlocal launch_reason
        connected, launch_reason = github_gate_check()
        return connected

    def _recheck() -> bool:
        nonlocal rechecks
        rechecks += 1
        connected, reason = github_gate_check(refresh=True)
        capture_github_gate_rechecked(
            connected=connected, reason_code=reason, recheck_count=rechecks
        )
        return connected

    def _record_choice(choice: ConnectGithubChoice | None) -> None:
        # Opening the dashboard is ``browser_open_requested``; a recheck is
        # recorded with its answer. Only leaving needs its own event here.
        # ``None`` is any dismissal; the picker does not say which key.
        if choice in (ConnectGithubChoice.OPEN, ConnectGithubChoice.CHECK):
            return
        capture_github_gate_exited(
            choice_label=ConnectGithubChoice.EXIT.value,
            method="menu" if choice is ConnectGithubChoice.EXIT else "dismissed",
            recheck_count=rechecks,
        )

    def _before_screen() -> None:
        capture_github_gate_prompted(reason_code=launch_reason or "check_unavailable")
        if before_screen is not None:
            before_screen()
        repl_clear_screen()

    return run_connect_github_gate(
        console,
        is_connected=_is_connected,
        recheck=_recheck,
        open_dashboard=lambda: open_github_dashboard(console, url),
        dashboard_url=url,
        on_prompted=_before_screen,
        on_choice=_record_choice,
    )


__all__ = ["github_connected", "github_gate_check", "open_github_dashboard", "pass_github_gate"]
