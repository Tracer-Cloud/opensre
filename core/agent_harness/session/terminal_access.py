"""Terminal facet accessors that work on SessionCore and interactive Session.

Gateway and other headless surfaces use :class:`~core.agent_harness.session.SessionCore`,
which has no REPL terminal facet. Slash dispatch and delegated CLI commands still
need the small slice of terminal state those paths touch (outcome hints).
These helpers read the shell terminal when present and fall back to
lightweight per-session state on headless sessions.
"""

from __future__ import annotations

from typing import Any


def session_terminal(session: Any) -> Any | None:
    return getattr(session, "terminal", None)


def exclusive_stdin_active(session: Any) -> bool:
    terminal = session_terminal(session)
    if terminal is not None:
        return bool(terminal.exclusive_stdin_active)
    return False


def trust_mode_enabled(session: Any) -> bool:
    terminal = session_terminal(session)
    if terminal is not None:
        return bool(terminal.trust_mode)
    return False


def pop_turn_outcome_hint(session: Any) -> str:
    terminal = session_terminal(session)
    if terminal is not None:
        pop_hint = getattr(terminal, "pop_turn_outcome_hint", None)
        if callable(pop_hint):
            hint = pop_hint()
            return hint.strip() if isinstance(hint, str) else ""
    hints = getattr(session, "_headless_turn_outcome_hints", None)
    if isinstance(hints, list) and hints:
        return str(hints.pop()).strip()
    return ""


def set_turn_outcome_hint(session: Any, hint: str) -> None:
    terminal = session_terminal(session)
    if terminal is not None:
        terminal.set_turn_outcome_hint(hint)
        return
    hints = getattr(session, "_headless_turn_outcome_hints", None)
    if hints is None:
        hints = []
        session._headless_turn_outcome_hints = hints
    hints.append(hint)


_ONBOARD_SLASH = "/onboard"


def set_auto_command(session: Any, command: str) -> None:
    terminal = session_terminal(session)
    if terminal is not None:
        terminal.set_auto_command(command)
        return
    set_turn_outcome_hint(
        session,
        f"Run `{command}` in the interactive shell (`uv run opensre`).",
    )


def execute_cli_onboard_on_missing_key(
    session: Any | None,
    message: str,
    *,
    provider: str | None = None,
) -> str | None:
    """Setup guidance for a missing-key or missing-endpoint *message*, else ``None``.

    The interactive shell (a session with a terminal facet) gets slash commands
    and ``/onboard`` queued for its next prompt. Every other session — ``opensre
    ask``, the gateway — gets ``opensre`` commands, plus account sign-in when the
    process is signed out; nothing is queued, since it has no prompt to run it.
    """
    from core.llm_invoke_errors import CommandSurface, remediate_llm_setup_failure

    if session is None or session_terminal(session) is None:
        from config.account import account_llm_route

        return remediate_llm_setup_failure(
            message,
            surface=CommandSurface.CLI,
            provider=provider,
            offer_account_login=account_llm_route() is None,
        )
    text = remediate_llm_setup_failure(message, surface=CommandSurface.SHELL, provider=provider)
    if text is None or exclusive_stdin_active(session):
        return text
    set_auto_command(session, _ONBOARD_SLASH)
    return text


def clear_pending_autosubmit(session: Any) -> None:
    """Drop queued REPL autosubmit when present (no-op on SessionCore).

    Shell ``/goal pause`` and the outer session-goal loop must clear a queued
    next turn without assuming ``session.terminal`` exists — gateway sessions
    are bare :class:`~core.agent_harness.session.SessionCore`.
    """
    terminal = session_terminal(session)
    if terminal is None:
        return
    if hasattr(terminal, "pending_prompt_default"):
        terminal.pending_prompt_default = None
    if hasattr(terminal, "pending_prompt_autosubmit"):
        terminal.pending_prompt_autosubmit = False
    if hasattr(terminal, "pending_prompt_plain_turn"):
        terminal.pending_prompt_plain_turn = False


__all__ = [
    "clear_pending_autosubmit",
    "exclusive_stdin_active",
    "execute_cli_onboard_on_missing_key",
    "pop_turn_outcome_hint",
    "session_terminal",
    "set_auto_command",
    "set_turn_outcome_hint",
    "trust_mode_enabled",
]
