"""Investigation action execution."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass
class ActionExecutionResult:
    """Result of executing an investigation action."""

    action_name: str
    success: bool
    data: dict
    error: str | None = None


def execute_actions(
    action_names: list[str],
    available_actions: dict[str, Any] | Iterable[Any],
    available_sources: dict[str, dict] | None = None,
) -> dict[str, ActionExecutionResult]:
    """
    Execute investigation actions by name.

    Args:
        action_names: List of action names to execute
        available_actions: Mapping or iterable of available actions
        available_sources: Optional dictionary of available data sources

    Returns:
        Dictionary mapping action names to execution results
    """
    if available_sources is None:
        available_sources = {}

    if isinstance(available_actions, dict):
        available_actions_map = available_actions
    else:
        available_actions_map = {action.name: action for action in available_actions}
    results: dict[str, ActionExecutionResult] = {}

    for action_name in action_names:
        if action_name not in available_actions_map:
            results[action_name] = ActionExecutionResult(
                action_name=action_name,
                success=False,
                data={},
                error=f"Unknown action: {action_name}",
            )
            continue

        action = available_actions_map[action_name]

        # Check availability if availability_check is defined
        if action.availability_check and not action.availability_check(available_sources):
            results[action_name] = ActionExecutionResult(
                action_name=action_name,
                success=False,
                data={},
                error="Action not available: required data sources not found",
            )
            continue

        try:
            # Extract parameters using parameter_extractor
            kwargs = action.parameter_extractor(available_sources)
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
                    error=data.get("error", "Unknown error")
                    if isinstance(data, dict)
                    else "Invalid response",
                )
        except Exception as e:
            results[action_name] = ActionExecutionResult(
                action_name=action_name,
                success=False,
                data={},
                error=str(e),
            )

    return results
