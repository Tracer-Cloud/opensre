"""Investigation prompt construction with available actions."""

from typing import Any

from pydantic import BaseModel


def _get_executed_sources(executed_hypotheses: list[dict[str, Any]]) -> set[str]:
    """Extract executed sources from hypotheses history."""
    executed_sources_set = set()
    for h in executed_hypotheses:
        sources = h.get("sources", [])
        if isinstance(sources, list):
            executed_sources_set.update(sources)
        single_source = h.get("source")
        if single_source:
            executed_sources_set.add(single_source)
    return executed_sources_set


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
    problem_md: str,
    investigation_recommendations: list[str],
    executed_hypotheses: list[dict[str, Any]],
    available_actions: list,
    available_sources: dict[str, dict],
) -> str:
    """
    Build the investigation prompt with rich action metadata.

    Args:
        problem_md: Problem statement markdown
        investigation_recommendations: Recommendations from previous analysis
        executed_hypotheses: History of executed hypotheses
        available_actions: Pre-computed actions list (already filtered by availability)
        available_sources: Dictionary of available data sources

    Returns:
        Formatted prompt string for LLM
    """
    executed_sources_set = _get_executed_sources(executed_hypotheses)
    executed_actions = [
        action.name
        for action in available_actions
        if action.source in executed_sources_set
    ]

    available_actions_filtered = [
        action for action in available_actions if action.name not in executed_actions
    ]

    problem_context = problem_md or "No problem statement available"
    recommendations = investigation_recommendations or []

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


def select_actions(
    actions: list,
    available_sources: dict[str, dict],
    executed_hypotheses: list[dict[str, Any]],
) -> tuple[list, list[str]]:
    """
    Select available actions based on sources and execution history.

    Args:
        actions: Candidate actions to filter
        available_sources: Dictionary mapping source type to parameters
        executed_hypotheses: History of executed hypotheses

    Returns:
        Tuple of (available_actions, available_action_names)
    """
    available_actions = [
        action
        for action in actions
        if action.availability_check is None or action.availability_check(available_sources)
    ]

    executed_actions_flat = set()
    for hyp in executed_hypotheses:
        actions = hyp.get("actions", [])
        if isinstance(actions, list):
            executed_actions_flat.update(actions)

    available_actions = [
        action for action in available_actions if action.name not in executed_actions_flat
    ]
    available_action_names = [action.name for action in available_actions]

    return available_actions, available_action_names


def plan_actions_with_llm(
    llm,
    plan_model: type[BaseModel],
    problem_md: str,
    investigation_recommendations: list[str],
    executed_hypotheses: list[dict[str, Any]],
    available_actions: list,
    available_sources: dict[str, dict],
):
    """
    Build the investigation prompt and invoke the LLM for a plan.

    Args:
        llm: LLM client
        plan_model: Pydantic model for structured output
        problem_md: Problem statement markdown
        investigation_recommendations: Recommendations from previous analysis
        executed_hypotheses: History of executed hypotheses
        available_actions: Filtered list of actions
        available_sources: Available data sources

    Returns:
        Structured plan from the LLM
    """
    prompt = build_investigation_prompt(
        problem_md=problem_md,
        investigation_recommendations=investigation_recommendations,
        executed_hypotheses=executed_hypotheses,
        available_actions=available_actions,
        available_sources=available_sources,
    )

    structured_llm = llm.with_structured_output(plan_model)
    return structured_llm.with_config(
        run_name="LLM – Plan evidence gathering"
    ).invoke(prompt)


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
