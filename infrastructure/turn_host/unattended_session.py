"""Sessions for turns nobody is watching: a remote prompt with no person on the other end."""

from __future__ import annotations

from core.agent_harness import SessionCore, SessionManager
from core.agent_harness.spi.session_state import withhold_capabilities

#: Capabilities an unattended turn never has: nothing interactive, nothing that switches
#: the runtime, and no CLI subprocess, whose output only a terminal could show.
UNATTENDED_DISABLED_CAPABILITIES = (
    "cli_commands",
    "hosted_gateway",
    "llm_provider",
    "slash_commands",
    "task_cancel",
)


class UnattendedSessions:
    """Opens one fresh session per unattended turn and closes it afterwards."""

    def __init__(self, manager: SessionManager | None = None) -> None:
        self._manager = manager or SessionManager()

    def open(self) -> SessionCore:
        session = self._manager.create(persistent_tasks=False, warm_integrations=False)
        restrict_to_unattended(session)
        return session

    def close(self, session: SessionCore) -> None:
        self._manager.close(session, wait_for_memory_extraction=False)


def restrict_to_unattended(session: SessionCore) -> None:
    """A question ends the turn as a pending choice instead of waiting for an answer."""
    withhold_capabilities(session, *UNATTENDED_DISABLED_CAPABILITIES)
    session.available_capabilities["ask_user_choice"] = ("deferred",)


__all__ = ["UNATTENDED_DISABLED_CAPABILITIES", "UnattendedSessions", "restrict_to_unattended"]
