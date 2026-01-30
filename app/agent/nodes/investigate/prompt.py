"""Investigation prompt construction with available actions."""

from app.agent.state import InvestigationState
from app.agent.tools.tool_actions.investigation_actions import get_available_actions
from app.agent.utils import get_executed_sources


def _build_available_sources_hint(available_sources: dict[str, dict]) -> str:
    """
    Build hints for all available data sources.

    Args:
        available_sources: Dictionary mapping source type to parameters

    Returns:
        Formatted string with hints for available sources
    """
    hints = []

    if "cloudwatch" in available_sources:
        cw = available_sources["cloudwatch"]
        hints.append(
            f"""CloudWatch Logs Available:
- Log Group: {cw.get('log_group')}
- Log Stream: {cw.get('log_stream')}
- Region: {cw.get('region', 'us-east-1')}
- Use get_cloudwatch_logs to fetch error logs and tracebacks"""
        )

    if "s3" in available_sources:
        s3 = available_sources["s3"]
        hints.append(
            f"""S3 Storage Available:
- Bucket: {s3.get('bucket')}
- Prefix: {s3.get('prefix', 'N/A')}
- Use check_s3_marker to verify pipeline completion markers"""
        )

    if "local_file" in available_sources:
        local = available_sources["local_file"]
        hints.append(
            f"""Local File Available:
- Log File: {local.get('log_file')}
- Note: Local file logs can be read directly"""
        )

    if "tracer_web" in available_sources:
        tracer = available_sources["tracer_web"]
        hints.append(
            f"""Tracer Web Platform Available:
- Trace ID: {tracer.get('trace_id')}
- Run URL: {tracer.get('run_url', 'N/A')}
- Use get_failed_jobs, get_failed_tools, get_error_logs to fetch execution data"""
        )

    if hints:
        return "\n\n" + "\n\n".join(hints) + "\n"
    return ""


def build_investigation_prompt(
    state: InvestigationState,
    available_actions: list | None = None,
    available_sources: dict[str, dict] | None = None,
) -> str:
    """
    Build the investigation prompt with rich action metadata.

    Args:
        state: Current investigation state
        available_actions: Optional pre-computed actions list (already filtered by availability)
        available_sources: Optional dictionary of available data sources

    Returns:
        Formatted prompt string for LLM
    """
    if available_actions is None:
        available_actions = get_available_actions()

    if available_sources is None:
        available_sources = {}

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

    sources_hint = _build_available_sources_hint(available_sources)

    prompt = f"""You are investigating a data pipeline incident.

Problem Context:
{problem_context}
{sources_hint}
Available Investigation Actions:
{actions_description if actions_description else "No actions available"}

Executed Actions: {', '.join(executed_actions) if executed_actions else "None"}

Recommendations from previous analysis:
{chr(10).join(f"- {r}" for r in recommendations) if recommendations else "None"}

Task: Select the most relevant actions to execute now based on the problem context.
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
