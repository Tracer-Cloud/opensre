"""LangGraph entry point for the adaptive-window node.

The node sits between ``diagnose`` and ``plan_actions`` on the
loop-back edge of the investigation pipeline. It runs ONLY when the
loop is continuing — terminal paths bypass it.

The actual decision logic lives in ``app.nodes.adapt_window.rules`` and
is imported here. Keeping the LangGraph wrapper thin means the rule can
be tested in isolation against plain dicts.
"""

import logging
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from app.nodes.adapt_window.rules import adapt_incident_window
from app.output import debug_print
from app.state import InvestigationState

logger = logging.getLogger(__name__)


@traceable(name="node_adapt_window")
def node_adapt_window(
    state: InvestigationState,
    config: Optional[RunnableConfig] = None,  # noqa: ARG001,UP007,UP045
) -> dict:
    """Apply the adaptive-window rules and return a state delta.

    Returns an empty dict (LangGraph's "no state change" signal) when no
    rule fires. Otherwise returns a dict with ``incident_window`` (new
    window) and ``incident_window_history`` (old window appended).

    Pure wrapper around ``adapt_incident_window`` — no I/O, no LLM. The
    only side effect is a debug log line so operators can audit when an
    expansion happened during a run.
    """
    delta = adapt_incident_window(dict(state))
    if delta:
        new_window = delta.get("incident_window") or {}
        history = delta.get("incident_window_history") or []
        last_entry = history[-1] if history else {}
        reason = last_entry.get("replaced_reason", "") if isinstance(last_entry, dict) else ""
        debug_print(
            "adapt_window: widened incident window "
            f"since={new_window.get('since')} until={new_window.get('until')} "
            f"reason={reason}"
        )
        logger.info(
            "adapt_window: widened incident window since=%s until=%s reason=%s",
            new_window.get("since"),
            new_window.get("until"),
            reason,
        )
    return delta
