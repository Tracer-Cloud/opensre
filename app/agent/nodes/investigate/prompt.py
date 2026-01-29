"""Investigation prompt construction with available actions."""

from app.agent.state import InvestigationState
from app.agent.tools.tool_actions.investigation_actions import get_available_actions
from app.agent.utils import get_executed_sources


def _extract_cloudwatch_hint(state: InvestigationState) -> str:
    """Extract CloudWatch log availability hint from alert."""
    raw_alert = state.get("raw_alert", {})
    if isinstance(raw_alert, dict):
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        if annotations and annotations.get("cloudwatch_log_group"):
            return f"""
CloudWatch Logs Available:
- Log Group: {annotations.get('cloudwatch_log_group')}
- Log Stream: {annotations.get('cloudwatch_log_stream')}
- Use get_cloudwatch_logs to fetch error logs and tracebacks
"""
    return ""


def build_investigation_prompt(
    state: InvestigationState, available_actions: list | None = None
) -> str:
    """
    Build the investigation prompt with rich action metadata.

    Args:
        state: Current investigation state
        available_actions: Optional pre-computed actions list

    Returns:
        Formatted prompt string for LLM
    """
    if available_actions is None:
        available_actions = get_available_actions()

    executed_sources_set = get_executed_sources(state)
    executed_actions = [
        action.name
        for action in available_actions
        if action.source in executed_sources_set
    ]

    available_actions_filtered = [
        action for action in available_actions if action.name not in executed_actions
    ]

    problem_context = state.get("problem_md", "No problem statement available")
    recommendations = state.get("investigation_recommendations", [])

    actions_description = "\n\n".join(
        _format_action_metadata(action) for action in available_actions_filtered
    )

    cloudwatch_hint = _extract_cloudwatch_hint(state)

    prompt = f"""You are investigating a data pipeline incident.

Problem Context:
{problem_context}
{cloudwatch_hint}
Available Investigation Actions:
{actions_description if actions_description else "No actions available"}

Executed Actions: {', '.join(executed_actions) if executed_actions else "None"}

Recommendations from previous analysis:
{chr(10).join(f"- {r}" for r in recommendations) if recommendations else "None"}

Task: Select the most relevant actions to execute now based on the problem context.
IMPORTANT: If CloudWatch logs are available above, you MUST use get_cloudwatch_logs to retrieve error logs.
Consider what information would help diagnose the root cause.
"""
    return prompt


def _format_action_metadata(action) -> str:
    """Format a single action's metadata for the prompt."""
    inputs_desc = "\n    ".join(
        f"- {param}: {desc}" for param, desc in action.inputs.items()
    )
    outputs_desc = "\n    ".join(
        f"- {field}: {desc}" for field, desc in action.outputs.items()
    )
    use_cases_desc = "\n    ".join(f"- {uc}" for uc in action.use_cases)

    return f"""Action: {action.name}
  Description: {action.description}
  Source: {action.source}
  Required Inputs:
    {inputs_desc}
  Returns:
    {outputs_desc}
  Use When:
    {use_cases_desc}"""
