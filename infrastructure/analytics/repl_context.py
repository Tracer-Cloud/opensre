"""REPL-scoped analytics context for joinable lifecycle events."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar, Token

_CLI_SESSION_ID: ContextVar[str | None] = ContextVar("cli_session_id", default=None)
_CLI_TURN_KIND: ContextVar[str | None] = ContextVar("cli_turn_kind", default=None)
_PROMPT_TURN_ID: ContextVar[str | None] = ContextVar("prompt_turn_id", default=None)


def get_cli_session_id() -> str | None:
    """Return the active interactive-shell session id, if any."""
    return _CLI_SESSION_ID.get()


def get_cli_turn_kind() -> str | None:
    """Return the active REPL turn kind for lifecycle joins, if any."""
    return _CLI_TURN_KIND.get()


def get_prompt_turn_id() -> str | None:
    """Return the active prompt-turn correlation id, if any."""
    return _PROMPT_TURN_ID.get()


def bind_cli_session_id(session_id: str | None) -> Token[str | None]:
    """Bind ``cli_session_id`` for the current async/task context."""
    return _CLI_SESSION_ID.set(session_id)


def bind_cli_turn_kind(turn_kind: str | None) -> Token[str | None]:
    """Bind ``cli_turn_kind`` for the current async/task context."""
    return _CLI_TURN_KIND.set(turn_kind)


def bind_prompt_turn_id(prompt_turn_id: str | None) -> Token[str | None]:
    """Bind ``prompt_turn_id`` for the current async/task context."""
    return _PROMPT_TURN_ID.set(prompt_turn_id)


def reset_cli_session_id(token: Token[str | None]) -> None:
    """Restore the previous ``cli_session_id`` binding."""
    _CLI_SESSION_ID.reset(token)


def reset_prompt_turn_id(token: Token[str | None]) -> None:
    """Restore the enclosing turn's correlation id."""
    _PROMPT_TURN_ID.reset(token)


@contextlib.contextmanager
def bound_repl_turn_context(
    *,
    session_id: str | None = None,
    turn_kind: str | None = None,
    prompt_turn_id: str | None = None,
) -> Iterator[None]:
    """Bind REPL-scoped analytics context for one turn or background task."""
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    if session_id is not None:
        tokens.append((_CLI_SESSION_ID, bind_cli_session_id(session_id)))
    if turn_kind is not None:
        tokens.append((_CLI_TURN_KIND, bind_cli_turn_kind(turn_kind)))
    if prompt_turn_id is not None:
        tokens.append((_PROMPT_TURN_ID, bind_prompt_turn_id(prompt_turn_id)))
    try:
        yield
    finally:
        for context_var, token in reversed(tokens):
            context_var.reset(token)
