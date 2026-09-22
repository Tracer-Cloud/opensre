"""What gateway chat does not offer.

The list is the gateway's product decision; recording it on a session is the
harness's job (``spi.session_state.withhold_capabilities``), because the
harness owns the capability mapping and reads it when planning a turn.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.spi.session_state import withhold_capabilities

UNSUPPORTED_GATEWAY_CAPABILITIES = (
    # The hosted gateway has no signed-in account: the tools that reach it from a laptop
    # cannot run on it.
    "hosted_gateway",
    "llm_provider",
    "task_cancel",
)


def ensure_gateway_capability_policy(session: Any) -> None:
    """Record the capabilities gateway chat withholds on ``session``."""
    withhold_capabilities(session, *UNSUPPORTED_GATEWAY_CAPABILITIES)


__all__ = [
    "UNSUPPORTED_GATEWAY_CAPABILITIES",
    "ensure_gateway_capability_policy",
]
