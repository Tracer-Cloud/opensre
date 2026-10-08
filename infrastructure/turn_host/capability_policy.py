"""Record the gateway's scheduler capabilities and withhold shell account tools."""

from __future__ import annotations

from typing import Any

from config.constants.capabilities import (
    HOSTED_GATEWAY_CAPABILITY,
    MCP_GATEWAY_MUTATION_CAPABILITY,
    SCHEDULER_HOST_CAPABILITY,
    SCHEDULER_HOST_IN_PROCESS,
)
from core.agent_harness.spi.session_state import withhold_capabilities


def ensure_gateway_capability_policy(session: Any, *, hosts_scheduler: bool = False) -> None:
    """Record scheduler hosting and withhold tools that need a signed-in account.

    A gateway that runs the scheduler in-process says so: a scheduling tool then
    registers its task with the store instead of installing an OS-level service
    the container cannot run. The hosted-gateway tools call the OpenSRE app with
    this machine's account token. The gateway has no such sign-in, so those
    tools stay off its turns.
    """
    withhold_capabilities(
        session,
        HOSTED_GATEWAY_CAPABILITY,
        MCP_GATEWAY_MUTATION_CAPABILITY,
    )
    if hosts_scheduler:
        capabilities = getattr(session, "available_capabilities", None)
        if isinstance(capabilities, dict):
            capabilities[SCHEDULER_HOST_CAPABILITY] = (SCHEDULER_HOST_IN_PROCESS,)


__all__ = [
    "ensure_gateway_capability_policy",
]
