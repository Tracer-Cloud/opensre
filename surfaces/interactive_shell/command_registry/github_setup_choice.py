"""Resume onboarding after the web-app GitHub setup menu.

The menu rows are not slash commands. Open launches the organization home
page and asks again. Continue re-reads the app and, once GitHub is connected,
enters the skill that was waiting.
"""

from __future__ import annotations

import webbrowser
from typing import Any

from rich.console import Console
from rich.markup import escape

from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.tools import ActionToolScope
from infrastructure.terminal import theme as ui_theme
from integrations.account_integrations import (
    account_github_connection,
    github_onboarding_setup_url,
)
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from tools.interactive_shell.actions.github_onboarding_gate import (
    github_onboarding_note,
    parse_github_onboarding_action,
    queue_github_onboarding_menu,
)
from tools.interactive_shell.actions.skill_entry import enter_skill

_CHOOSE_COMMAND = "/choose"


def _drop_choose_autosubmit(session: Any) -> None:
    """Drop a ``/choose`` queued while this turn already owns stdin."""
    terminal = getattr(session, "terminal", None)
    if terminal is None or not getattr(terminal, "pending_prompt_autosubmit", False):
        return
    if getattr(terminal, "pending_prompt_default", None) != _CHOOSE_COMMAND:
        return
    terminal.pending_prompt_default = None
    terminal.pending_prompt_autosubmit = False
    terminal.pending_prompt_plain_turn = False


def _present_follow_up_menu(session: Any, console: Console) -> None:
    """Render a menu queued from inside ``/choose`` before that turn ends.

    Autosubmitting another ``/choose`` starts the prompt, which blocks in a
    raw read. Suspending it to run the menu waits on that read, so the menu
    never appears. This turn already owns stdin; show the menu here.
    """
    if getattr(session, "pending_user_choice", None) is None:
        return
    _drop_choose_autosubmit(session)
    from surfaces.interactive_shell.command_registry.choice_prompt import _cmd_choose

    _cmd_choose(session, console, [])


class _TtyProbe:
    def tty_interactive(self) -> bool:
        """The shell that rendered this menu can render the next one."""
        return repl_tty_interactive()


def handle_github_onboarding_choice(
    session: Any,
    console: Console,
    pending: PendingUserChoice,
    picked: str,
) -> bool:
    """Handle one setup-menu pick. Return False when ``picked`` is some other menu."""
    parsed = parse_github_onboarding_action(pending.commands.get(picked, ""))
    if parsed is None:
        return False
    kind, skill_name = parsed
    if kind == "open":
        _open_setup_page(session, console, skill_name)
        return True
    _continue_after_setup(session, console, skill_name)
    return True


def _open_setup_page(session: Any, console: Console, skill_name: str) -> None:
    url = github_onboarding_setup_url()
    if not url:
        console.print(f"[{ui_theme.ERROR}]OpenSRE could not build the GitHub setup link.[/]")
        return
    try:
        opened = bool(webbrowser.open(url))
    except Exception:
        opened = False
    if opened:
        console.print(f"[{ui_theme.DIM}]Opened {escape(url)}.[/]")
    else:
        console.print(f"[{ui_theme.DIM}]Open {escape(url)} and connect GitHub.[/]")
    queue_github_onboarding_menu(
        session,
        skill_name,
        note=github_onboarding_note(url),
    )
    _present_follow_up_menu(session, console)


def _continue_after_setup(session: Any, console: Console, skill_name: str) -> None:
    status = account_github_connection(refresh=True)
    if status in {"connected", "signed_out"}:
        scope = ActionToolScope(
            session=session,
            console=console,
            is_tty=True,
            slash_ports=_TtyProbe(),
        )
        result = enter_skill(skill_name, scope)
        if not result.get("ok"):
            console.print(
                f"[{ui_theme.ERROR}]Could not resume {escape(skill_name)}: "
                f"{escape(str(result.get('error', 'unknown error')))}.[/]"
            )
            return
        _present_follow_up_menu(session, console)
        return
    url = github_onboarding_setup_url()
    if not url:
        console.print(f"[{ui_theme.ERROR}]OpenSRE could not build the GitHub setup link.[/]")
        return
    queue_github_onboarding_menu(
        session,
        skill_name,
        note=github_onboarding_note(
            url,
            still_missing=status == "missing",
            unreachable=status == "unknown",
        ),
    )
    _present_follow_up_menu(session, console)


__all__ = ["handle_github_onboarding_choice"]
