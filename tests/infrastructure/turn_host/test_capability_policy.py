"""The gateway records whether it hosts the scheduler, and the repair-loop tool reads it."""

from __future__ import annotations

from config.constants.capabilities import SCHEDULER_HOST_CAPABILITY, SCHEDULER_HOST_IN_PROCESS
from core.agent_harness import SessionCore
from core.agent_harness.tools import ActionToolScope, capability_values
from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY
from core.tool import AgentToolContext
from infrastructure.turn_host.capability_policy import ensure_gateway_capability_policy
from integrations.github.tools.ci_repair_loop.tool import _scheduler_in_process


def _context(session: SessionCore) -> AgentToolContext:
    scope = ActionToolScope(session=session, console=None)
    return AgentToolContext(
        resolved_integrations={}, resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: scope}
    )


def test_a_scheduler_hosting_gateway_tells_the_repair_loop_tool_and_a_chat_gateway_does_not() -> (
    None
):
    # Arrange
    hosting = SessionCore()
    chat_only = SessionCore()

    # Act
    ensure_gateway_capability_policy(hosting, hosts_scheduler=True)
    ensure_gateway_capability_policy(chat_only)

    # Assert: the fact is on the session and the tool reads it from its runtime context
    assert capability_values(hosting, SCHEDULER_HOST_CAPABILITY) == (SCHEDULER_HOST_IN_PROCESS,)
    assert capability_values(chat_only, SCHEDULER_HOST_CAPABILITY) == ()
    assert _scheduler_in_process(_context(hosting)) is True
    assert _scheduler_in_process(_context(chat_only)) is False
    assert _scheduler_in_process(None) is False
