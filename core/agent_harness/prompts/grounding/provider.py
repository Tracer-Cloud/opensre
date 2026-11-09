"""The default prompt-context provider: the surface one session's turns run on."""

from __future__ import annotations

from typing import Any


class DefaultPromptContextProvider:
    """:class:`core.agent_harness.ports.PromptContextProvider` bound to one session."""

    def __init__(self, session: Any, *, surface: str = "interactive_shell") -> None:
        self._session = session
        self._surface = surface

    def bind_session(self, session: Any) -> None:
        """Point this provider at a freshly resolved session (gateway reuse)."""
        self._session = session

    def surface(self) -> str:
        return self._surface


__all__ = ["DefaultPromptContextProvider"]
