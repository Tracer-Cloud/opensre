"""The gateway records whether it hosts the scheduler, and scheduling tools read it."""

from __future__ import annotations

from config.constants.capabilities import SCHEDULER_HOST_CAPABILITY, SCHEDULER_HOST_IN_PROCESS
from core.agent_harness import SessionCore
from core.agent_harness.tools import capability_values_from_sources
from infrastructure.turn_host.capability_policy import ensure_gateway_capability_policy
from integrations.github.tools.ci_repair_loop.tool import _credentials


def _sources(session: SessionCore) -> dict[str, dict]:
    return {"_action_session": {"available_capabilities": session.available_capabilities}}


def test_a_scheduler_hosting_gateway_tells_the_repair_loop_tool_and_a_chat_gateway_does_not() -> (
    None
):
    # Arrange
    hosting = SessionCore()
    chat_only = SessionCore()

    # Act
    ensure_gateway_capability_policy(hosting, hosts_scheduler=True)
    ensure_gateway_capability_policy(chat_only)

    # Assert: the capability is on the session, readable through the tool's sources view
    assert hosting.available_capabilities[SCHEDULER_HOST_CAPABILITY] == (SCHEDULER_HOST_IN_PROCESS,)
    assert SCHEDULER_HOST_CAPABILITY not in chat_only.available_capabilities
    assert capability_values_from_sources(_sources(hosting), SCHEDULER_HOST_CAPABILITY) == (
        SCHEDULER_HOST_IN_PROCESS,
    )
    assert capability_values_from_sources(_sources(chat_only), SCHEDULER_HOST_CAPABILITY) == ()
    assert _credentials(_sources(hosting))["scheduler_in_process"] is True
    assert _credentials(_sources(chat_only))["scheduler_in_process"] is False
