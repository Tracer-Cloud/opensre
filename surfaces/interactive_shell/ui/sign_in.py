"""Mandatory account sign-in screen for the interactive shell.

Shown in place of the composer when the user is not signed in: the launch
banner, a welcome box, and a sign-in/stay-signed-out menu. Authentication is injected — this
module owns only the presentation and the choice loop, not the login flow — so
the web-app sign-in can plug into the ``is_signed_in`` / ``login`` seams without
this screen depending on it.
"""

from __future__ import annotations

import enum
from collections.abc import Callable

from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from config.constants import SIGN_IN_PROMPT, WELCOME_DESCRIPTION, WELCOME_TITLE
from infrastructure.terminal.theme import DIM, ERROR, HIGHLIGHT, SECONDARY, TEXT
from surfaces.shared.terminal.banner import animate_launch_wordmark, build_launch_banner
from surfaces.shared.terminal.components.choice_menu import repl_choose_one, repl_tty_interactive


class SignInChoice(enum.StrEnum):
    """The actions offered on the forced sign-in screen.

    ``OWN_MODEL`` is offered only when the user already configured their own
    provider, so a fresh install still sees the two-way sign-in choice.
    """

    LOGIN = "Sign in or create account"
    OWN_MODEL = "Continue with my own model"
    EXIT = "Exit and stay signed out"


def build_welcome_box() -> RenderableType:
    """The bordered welcome box: blue title over the one-line product description."""
    body = Text()
    body.append(WELCOME_TITLE, style=f"bold {HIGHLIGHT}")
    body.append("\n")
    body.append(WELCOME_DESCRIPTION, style=str(TEXT))
    # No fixed width: the panel expands to the full terminal width.
    return Panel(body, box=ROUNDED, border_style=str(DIM), padding=(1, 2))


def render_sign_in_screen(console: Console) -> None:
    """Paint the launch banner, the welcome box, and the sign-in prompt line."""
    screen = Group(
        build_launch_banner(console),
        build_welcome_box(),
        Text(),
        Text(SIGN_IN_PROMPT, style=str(SECONDARY)),
    )
    animate_launch_wordmark(console)
    console.print(screen)


def prompt_login_or_exit(*, offer_own_model: bool = False) -> SignInChoice | None:
    """Show the sign-in menu; return the choice, or ``None`` on Esc.

    The sign-in prompt is already printed by ``render_sign_in_screen`` above the
    menu, so the menu itself carries no title (avoids repeating the prompt).
    """
    offered = [SignInChoice.LOGIN]
    if offer_own_model:
        offered.append(SignInChoice.OWN_MODEL)
    offered.append(SignInChoice.EXIT)
    picked = repl_choose_one(
        title="",
        choices=[(choice, choice) for choice in offered],
        numbered=False,
    )
    return next((choice for choice in offered if picked == choice), None)


def run_sign_in_gate(
    console: Console,
    *,
    is_signed_in: Callable[[], bool],
    login: Callable[[], bool],
    has_own_provider: Callable[[], bool] = lambda: False,
    on_own_provider: Callable[[], None] = lambda: None,
) -> bool:
    """Gate the REPL behind sign-in; return ``True`` to proceed, ``False`` to exit.

    Returns immediately when already signed in. Otherwise renders the sign-in
    screen and loops the menu. A user who configured their own LLM provider is
    offered a third choice that enters the shell signed out, which is the only
    way to run a local model: an account session pins the shell to the hosted
    route. Picking it calls ``on_own_provider``, which drops any stored hosted
    route so a rejected or unvalidated session cannot override the choice. On
    non-interactive stdin the gate fails closed and prints the command that can
    establish an account.
    """
    if is_signed_in():
        return True
    if not repl_tty_interactive():
        console.print(
            f"[{ERROR}]An active OpenSRE account is required for the interactive shell.[/]"
        )
        console.print("Run [bold]opensre account login[/bold] from an interactive terminal.")
        return False
    render_sign_in_screen(console)
    offer_own_model = has_own_provider()
    while True:
        choice = prompt_login_or_exit(offer_own_model=offer_own_model)
        if choice is SignInChoice.LOGIN:
            if login():
                return True
            continue  # login failed — offer the choice again
        if choice is SignInChoice.OWN_MODEL:
            on_own_provider()
            console.print(f"[{DIM}]Signed out. Using your configured LLM provider.[/]")
            return True
        return False  # Exit or Esc


__all__ = [
    "SignInChoice",
    "build_welcome_box",
    "prompt_login_or_exit",
    "render_sign_in_screen",
    "run_sign_in_gate",
]
