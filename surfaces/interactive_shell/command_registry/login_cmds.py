"""Provider connection menus and delegation to the canonical login CLI."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from core.agent_harness.spi.session_state import session_terminal
from surfaces.interactive_shell.runtime import Session
from surfaces.shared.llm_setup.auth_menu import prompt_auth_provider
from surfaces.shared.llm_setup.login_menu import prompt_connection_target, prompt_login_action


def cmd_login(
    session: Session, console: Console, args: list[str], *, run_cli: Callable[..., bool]
) -> bool:
    """Collect missing interactive choices locally before starting the CLI child."""
    selected = list(args)
    if session_terminal(session) is not None:
        if not selected:
            action = prompt_login_action()
            if action is None:
                return True
            selected = [action]
        if selected == ["connect"]:
            profile = prompt_auth_provider()
            if profile is None:
                return True
            selected.append(profile.name)
        elif selected in (["verify"], ["disconnect"]):
            provider = prompt_connection_target(selected[0])
            if provider is None:
                return True
            selected.append(provider)
    capture = bool(selected and selected[0] in {"list", "status", "verify", "disconnect"})
    return run_cli(console, ["login", *selected], capture_output=capture, session=session)


def cmd_auth(
    session: Session, console: Console, args: list[str], *, run_cli: Callable[..., bool]
) -> bool:
    """Preserve legacy auth invocation semantics while keeping its menus responsive."""
    selected = list(args)
    if selected == ["login"] and session_terminal(session) is not None:
        profile = prompt_auth_provider()
        if profile is None:
            return True
        selected.append(profile.name)
    capture = not selected or selected[0].lower() in {"status", "verify", "logout"}
    return run_cli(console, ["auth", *selected], capture_output=capture, session=session)
