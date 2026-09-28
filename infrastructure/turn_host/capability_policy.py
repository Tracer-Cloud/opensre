"""What gateway chat does not offer.

The list is the gateway's product decision; recording it on a session is the
harness's job (``spi.session_state.withhold_capabilities``), because the
harness owns the capability mapping and reads it when planning a turn.
"""

from __future__ import annotations

from typing import Any

from config.constants.capabilities import SCHEDULER_HOST_CAPABILITY, SCHEDULER_HOST_IN_PROCESS
from core.agent_harness.spi.session_state import withhold_capabilities

UNSUPPORTED_GATEWAY_CAPABILITIES = (
    # The hosted gateway has no signed-in account: the tools that reach it from a laptop
    # cannot run on it.
    "hosted_gateway",
    "llm_provider",
    "task_cancel",
)


def ensure_gateway_capability_policy(session: Any, *, hosts_scheduler: bool = False) -> None:
    """Record what gateway chat withholds on ``session``, and whether it hosts the scheduler.

    A gateway that runs the scheduler in-process says so: a scheduling tool then
    registers its task with the store instead of installing an OS-level service
    the container cannot run.
    """
    withhold_capabilities(session, *UNSUPPORTED_GATEWAY_CAPABILITIES)
    if hosts_scheduler:
        capabilities = getattr(session, "available_capabilities", None)
        if isinstance(capabilities, dict):
            capabilities[SCHEDULER_HOST_CAPABILITY] = (SCHEDULER_HOST_IN_PROCESS,)


__all__ = [
    "UNSUPPORTED_GATEWAY_CAPABILITIES",
    "ensure_gateway_capability_policy",
]
