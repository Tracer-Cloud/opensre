"""Mandatory GitHub connection screen for the interactive shell.

Shown after sign-in, in place of the composer and the startup menu, while the
signed-in member sees no connected GitHub account: OpenSRE's hosted model and
gateways refuse every request until one is connected, the workspace's or the
member's own. The check and the browser open are injected — this module owns
only the presentation and the choice loop.
"""

from __future__ import annotations

import enum
from collections.abc import Callable

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from infrastructure.terminal.theme import DIM, ERROR, HIGHLIGHT, SECONDARY, TEXT, WARNING
from surfaces.shared.terminal.banner import build_launch_banner
from surfaces.shared.terminal.components.choice_menu import repl_choose_one, repl_tty_interactive

CONNECT_GITHUB_TITLE = "Connect GitHub to continue"
CONNECT_GITHUB_DESCRIPTION = (
    "OpenSRE works on your repositories, so it answers nothing until GitHub is "
    "connected. Connect your own account on the OpenSRE dashboard, or ask a "
    "workspace admin to connect one for everyone."
)
CONNECT_GITHUB_PROMPT = "Connect GitHub on the dashboard, then come back here to continue."


class ConnectGithubChoice(enum.StrEnum):
    """The actions offered on the GitHub connection screen."""

    OPEN = "Open the dashboard to connect GitHub"
    CHECK = "I've connected GitHub, continue"
    EXIT = "Exit"


def render_connect_github_screen(console: Console) -> None:
    """Paint the launch banner, the connect-GitHub box, and the prompt line."""
    body = Text()
    body.append(CONNECT_GITHUB_TITLE, style=f"bold {HIGHLIGHT}")
    body.append("\n")
    body.append(CONNECT_GITHUB_DESCRIPTION, style=str(TEXT))
    console.print(
        Group(
            build_launch_banner(console),
            # No fixed width: the panel expands to the full terminal width.
            Panel(body, box=ROUNDED, border_style=str(DIM), padding=(1, 2)),
            Text(),
            Text(CONNECT_GITHUB_PROMPT, style=str(SECONDARY)),
        )
    )


def prompt_connect_github(*, focus: ConnectGithubChoice) -> ConnectGithubChoice | None:
    """Show the open/continue/exit menu with ``focus`` selected; ``None`` on Esc."""
    picked = repl_choose_one(
        title="",
        choices=[(choice, choice) for choice in ConnectGithubChoice],
        initial_value=focus,
        numbered=False,
    )
    return ConnectGithubChoice(picked) if picked in set(ConnectGithubChoice) else None


def run_connect_github_gate(
    console: Console,
    *,
    is_connected: Callable[[], bool],
    recheck: Callable[[], bool],
    open_dashboard: Callable[[], None],
    dashboard_url: str,
    on_prompted: Callable[[], None] | None = None,
) -> bool:
    """Gate the REPL behind a connected GitHub account; ``True`` to proceed, ``False`` to exit.

    Returns at once when ``is_connected``. Otherwise the screen loops until
    ``recheck`` (which asks the app again) confirms a connection or the user
    leaves; Esc leaves too. On non-interactive stdin the gate fails closed and
    names ``dashboard_url``. ``on_prompted`` fires once, before the screen.
    """
    if is_connected():
        return True
    if not repl_tty_interactive():
        console.print(f"[{ERROR}]Connect GitHub before using the interactive shell.[/]")
        console.print(f"Connect it at {escape(dashboard_url)}, then run [bold]opensre[/bold].")
        return False
    if on_prompted is not None:
        on_prompted()
    render_connect_github_screen(console)
    focus = ConnectGithubChoice.OPEN
    while True:
        choice = prompt_connect_github(focus=focus)
        if choice is ConnectGithubChoice.OPEN:
            open_dashboard()
            focus = ConnectGithubChoice.CHECK
            continue
        if choice is ConnectGithubChoice.CHECK:
            if recheck():
                return True
            console.print(
                f"[{WARNING}]GitHub is not connected yet.[/] "
                f"[{SECONDARY}]Connect it at {escape(dashboard_url)}, then continue.[/]"
            )
            continue
        return False  # Exit or Esc


__all__ = [
    "CONNECT_GITHUB_DESCRIPTION",
    "CONNECT_GITHUB_TITLE",
    "ConnectGithubChoice",
    "prompt_connect_github",
    "render_connect_github_screen",
    "run_connect_github_gate",
]
