"""Investigation action execution."""

from dataclasses import dataclass

from app.agent.state import InvestigationState
from app.agent.tools.tool_actions.investigation_actions import get_available_actions


@dataclass
class ActionExecutionResult:
    """Result of executing an investigation action."""

    action_name: str
    success: bool
    data: dict
    error: str | None = None


def execute_actions(
    state: InvestigationState, action_names: list[str]
) -> dict[str, ActionExecutionResult]:
    """
    Execute investigation actions by name.

    Args:
        state: Current investigation state
        action_names: List of action names to execute

    Returns:
        Dictionary mapping action names to execution results
    """
    available_actions = {action.name: action for action in get_available_actions()}
    results: dict[str, ActionExecutionResult] = {}

    tracer_web_run = state.get("context", {}).get("tracer_web_run", {})
    trace_id = tracer_web_run.get("trace_id")

    for action_name in action_names:
        if action_name not in available_actions:
            results[action_name] = ActionExecutionResult(
                action_name=action_name,
                success=False,
                data={},
                error=f"Unknown action: {action_name}",
            )
            continue

        action = available_actions[action_name]

        if "trace_id" in action.requires and not trace_id:
            results[action_name] = ActionExecutionResult(
                action_name=action_name,
                success=False,
                data={},
                error="trace_id required but not found in state",
            )
            continue

        try:
            # Build kwargs based on action inputs
            kwargs = {}
            if "trace_id" in action.inputs:
                kwargs["trace_id"] = trace_id
            if "size" in action.inputs:
                kwargs["size"] = 500
            if "error_only" in action.inputs:
                kwargs["error_only"] = True

            data = action.function(**kwargs)

            if isinstance(data, dict) and "error" not in data:
                results[action_name] = ActionExecutionResult(
                    action_name=action_name,
                    success=True,
                    data=data,
                    error=None,
                )
            else:
                results[action_name] = ActionExecutionResult(
                    action_name=action_name,
                    success=False,
                    data=data if isinstance(data, dict) else {},
                    error=data.get("error", "Unknown error") if isinstance(data, dict) else "Invalid response",
                )
        except Exception as e:
            results[action_name] = ActionExecutionResult(
                action_name=action_name,
                success=False,
                data={},
                error=str(e),
            )

    return results
