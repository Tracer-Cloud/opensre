"""What an action tool implements and calls: the harness API for tool authors.

An action tool receives an :class:`ActionToolScope`, runs its body under it
with :func:`execute_with_action_context`, and may ask whether a capability is
available. Import-cheap: nothing here loads the agent loop.

The other API modules: :mod:`core.agent_harness` (embed), :mod:`core.agent_harness.ports`
(what a host implements), :mod:`core.agent_harness.spi` (what a host calls, by
role), :mod:`core.agent_harness.runtime` (build and run the agent).
"""

from __future__ import annotations

from core.agent_harness.tools.action_tools import registered_single_turn_tool_names
from core.agent_harness.tools.tool_context import (
    ActionToolScope,
    action_context_from_agent_context,
    action_scope_from_agent_context,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.agent_harness.turns.gather_observation import coerce_gathered_evidence

__all__ = [
    "ActionToolScope",
    "action_context_from_agent_context",
    "action_scope_from_agent_context",
    "capability_available_from_sources",
    "coerce_gathered_evidence",
    "execute_with_action_context",
    "registered_single_turn_tool_names",
]
