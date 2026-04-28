"""Parallel execution of investigate actions as hypotheses."""

import logging
from typing import cast

from langsmith import traceable

from app.nodes.investigate.execution.execute_actions import execute_actions
from app.output import get_tracker
from app.state import InvestigationState
from app.tools.investigation_registry import get_available_actions

logger = logging.getLogger(__name__)


@traceable(name="node_investigate_hypothesis")
def node_investigate_hypothesis(state: InvestigationState) -> dict:
    """Execute a single investigation action (hypothesis) in its own subgraph."""
    tracker = get_tracker()

    action_name = state.get("action_to_run")
    if not action_name:
        return {"hypothesis_results": []}

    tracker.start(f"investigate_{action_name}", f"Executing {action_name}")

    available_sources = cast(dict[str, dict[str, object]], state.get("available_sources", {}))
    all_actions = get_available_actions()
    actions_by_name = {action.name: action for action in all_actions}

    # Check if action is available
    if action_name not in actions_by_name:
        logger.warning("Planned action '%s' not found in action registry", action_name)
        tracker.complete(
            f"investigate_{action_name}",
            fields_updated=[],
            message=f"Skipped {action_name}: not in registry",
        )
        return {"hypothesis_results": []}

    available_actions = {action_name: actions_by_name[action_name]}

    # Execute single action
    execution_results = execute_actions([action_name], available_actions, available_sources)

    tracker.complete(
        f"investigate_{action_name}", fields_updated=[], message=f"Completed {action_name}"
    )

    # Return serialized result to be collected by merge node
    result = execution_results.get(action_name)
    if result:
        return {
            "hypothesis_results": [
                {
                    "action_name": action_name,
                    "success": result.success,
                    "data": result.data,
                    "error": result.error,
                }
            ]
        }
    return {"hypothesis_results": []}
