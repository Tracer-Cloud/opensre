"""Prompt input and stdin coordination policy for runtime turns."""

from __future__ import annotations

from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive


def _literal_slash_command_text(text: str) -> str | None:
    """Return literal ``/slash`` command text for command-shaped input, else ``None``.

    Terminal-UI policy only (spinner suppression and exclusive-stdin gating). The
    matching execution-side deterministic dispatch lives separately in
    ``core/agent_harness/turns/action_driver.py``; keep this function UI-only and do not
    grow natural-language intent inference here.
    """
    stripped = text.strip()
    return stripped if stripped.startswith("/") else None


_EXCLUSIVE_STDIN_MENU_COMMANDS: frozenset[str] = frozenset(
    {
        "/history",
        "/account",
        "/auth",
        # ``/choose`` renders the pending ask_user_choice arrow-key picker (raw
        # os.read on stdin), so the turn must own stdin exclusively.
        "/choose",
        # ``/demo`` queues onboarding; finish it before reading the queued prompt.
        "/demo",
        "/help",
        "/integrations",
        "/mcp",
        "/memory",
        "/model",
        "/tools",
        "/trust",
        "/verbose",
        "/?",
        # Table-outputting commands must suspend the persistent prompt first;
        # otherwise redraws trigger ESC[6n DSR queries whose CPR responses land
        # as literal keystrokes in the input buffer.
        "/doctor",
        "/version",
        "/verify",
        "/status",
        "/cost",
        "/tasks",
        "/loops",
        "/work",
        "/alerts",
        "/privacy",
        "/context",
        "/fleet",
        "/compact",
        "/sessions",
        "/resume",
        "/new",
        "/health",
    }
)
_EXCLUSIVE_STDIN_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("/integrations", "setup"),
        # ``remove`` drives a native inline arrow-key picker (raw os.read on
        # stdin). Without exclusive stdin the active prompt application steals
        # keystrokes and CPR responses leak into the next prompt buffer.
        ("/integrations", "remove"),
        ("/mcp", "connect"),
        ("/mcp", "disconnect"),
        ("/loops", "active"),
        ("/loops", "all"),
        ("/loops", "inbox"),
        ("/loops", "list"),
        ("/loops", "messages"),
    }
)
_WAIT_FOR_COMPLETION_COMMANDS: frozenset[str] = frozenset(
    {
        "/exit",
        "/quit",
        "/update",
        "/setup",
        "/onboard",
        "/config",
        "/account",
        "/auth",
        "/login",
        # ``/goal set|resume`` queues the condition as the next prompt turn.
        # Wait for the slash turn to finish so the work prompt renders as its
        # own ``[N] ❯`` line (not buried under the set paint / ``$`` echo).
        "/goal",
    }
)


def turn_should_show_spinner(text: str, _session: Session) -> bool:
    # UI-only: suppress the "thinking" spinner for literal slash commands, which
    # dispatch deterministically (no LLM) and would otherwise show a misleading
    # spinner. Natural-language turns still go through the action-agent LLM.
    return _literal_slash_command_text(text.strip()) is None


def turn_needs_exclusive_stdin(text: str, _session: Session) -> bool:
    if not repl_tty_interactive():
        return False

    t = text.strip()
    if not t:
        return False

    # Reserve stdin early for literal command-shaped input, but do not dispatch
    # here. This stays UI-only; deterministic slash execution lives in the turn
    # engine (core/agent_harness/turns/action_driver.py), not in this gating layer.
    dispatch_text = _literal_slash_command_text(t)
    if dispatch_text is None:
        return False

    parts = dispatch_text.split()
    if not parts:
        return False
    name = parts[0].lower()
    args = [arg.lower() for arg in parts[1:]]

    if name in _WAIT_FOR_COMPLETION_COMMANDS:
        return True
    if name == "/theme":
        return True
    if name in _EXCLUSIVE_STDIN_MENU_COMMANDS and not args:
        return True
    return bool(args and (name, args[0]) in _EXCLUSIVE_STDIN_SUBCOMMANDS)


__all__ = [
    "turn_needs_exclusive_stdin",
    "turn_should_show_spinner",
]
