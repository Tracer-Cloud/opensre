"""Centralized investigation actions registry with rich metadata extracted from tool actions."""

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agent.state import EvidenceSource


@dataclass
class InvestigationAction:
    """Metadata for an investigation action."""

    name: str
    description: str
    inputs: dict[str, str]  # Parameter name -> description
    outputs: dict[str, str]  # Output field -> description
    use_cases: list[str]  # When to use this action
    requires: list[str]  # Required inputs (e.g., trace_id)
    source: EvidenceSource  # Which source category this belongs to
    function: Callable[..., dict[str, Any]]  # The actual function to call
    availability_check: Callable[[dict[str, dict]], bool] | None = (
        None  # Check if action can run given available sources
    )
    parameter_extractor: Callable[[dict[str, dict]], dict[str, Any]] | None = (
        None  # Extract parameters from available sources
    )


def _extract_use_cases(docstring: str) -> list[str]:
    """Extract use cases from 'Useful for:' section in docstring."""
    if not docstring:
        return []
    useful_match = re.search(
        r"Useful for:\s*(.*?)(?:\n\n|\n[A-Z]|$)", docstring, re.DOTALL | re.IGNORECASE
    )
    if not useful_match:
        return []
    useful_text = useful_match.group(1).strip()
    use_cases = [line.strip().lstrip("- ") for line in useful_text.split("\n") if line.strip()]
    return use_cases


def _extract_inputs(docstring: str, func: Callable) -> dict[str, str]:
    """Extract input descriptions from 'Args:' section and function signature."""
    inputs = {}
    if not docstring:
        return inputs

    args_match = re.search(r"Args:\s*(.*?)(?:\n\n|\n[A-Z]|$)", docstring, re.DOTALL | re.IGNORECASE)
    if args_match:
        args_text = args_match.group(1).strip()
        for line in args_text.split("\n"):
            line = line.strip()
            if ":" in line:
                param, desc = line.split(":", 1)
                param = param.strip()
                desc = desc.strip()
                if param and desc:
                    inputs[param] = desc

    sig = inspect.signature(func)
    for param_name in sig.parameters:
        if param_name not in inputs:
            param = sig.parameters[param_name]
            if param.annotation != inspect.Parameter.empty:
                inputs[param_name] = f"Type: {param.annotation}"
            else:
                inputs[param_name] = "No description available"

    return inputs


def _extract_outputs(docstring: str) -> dict[str, str]:
    """Extract output descriptions from 'Returns:' section."""
    outputs = {}
    if not docstring:
        return outputs

    returns_match = re.search(
        r"Returns:\s*(.*?)(?:\n\n|\n[A-Z]|$)", docstring, re.DOTALL | re.IGNORECASE
    )
    if returns_match:
        returns_text = returns_match.group(1).strip()
        if "Dictionary with" in returns_text:
            desc = returns_text.replace("Dictionary with", "").strip()
            outputs["result"] = desc
        else:
            outputs["result"] = returns_text

    return outputs


def _extract_description(docstring: str) -> str:
    """Extract the main description (first line or paragraph)."""
    if not docstring:
        return ""
    lines = docstring.strip().split("\n")
    first_line = lines[0].strip()
    if first_line and not first_line.startswith("Useful for") and not first_line.startswith("Args"):
        return first_line
    return ""


def _build_investigation_action(
    name: str,
    func: Callable,
    source: EvidenceSource,
    requires: list[str] | None = None,
    availability_check: Callable[[dict[str, dict]], bool] | None = None,
    parameter_extractor: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
) -> InvestigationAction:
    """Build InvestigationAction from function and metadata."""
    docstring = inspect.getdoc(func) or ""
    description = _extract_description(docstring)
    use_cases = _extract_use_cases(docstring)
    inputs = _extract_inputs(docstring, func)
    outputs = _extract_outputs(docstring)

    if requires is None:
        requires = []
        sig = inspect.signature(func)
        for param_name, param in sig.parameters.items():
            if param.default == inspect.Parameter.empty:
                requires.append(param_name)

    return InvestigationAction(
        name=name,
        description=description,
        inputs=inputs,
        outputs=outputs,
        use_cases=use_cases,
        requires=requires,
        source=source,
        function=func,
        availability_check=availability_check,
        parameter_extractor=parameter_extractor,
    )


def get_available_actions() -> list[InvestigationAction]:
    """
    Get all available investigation actions with rich metadata.

    Metadata is extracted from the individual tool action functions' docstrings.
    This provides structured information about what actions are available,
    what they require as input, what they return, and when to use them.
    """
    from app.agent.tools.tool_actions.cloudwatch_actions import get_cloudwatch_logs
    from app.agent.tools.tool_actions.lambda_actions import (
        get_lambda_errors,
        get_lambda_invocation_logs,
    )
    from app.agent.tools.tool_actions.s3_actions import (
        check_s3_marker,
        inspect_s3_object,
        list_s3_objects,
    )
    from app.agent.tools.tool_actions.tracer_jobs import (
        get_failed_jobs,
        get_failed_tools,
    )
    from app.agent.tools.tool_actions.tracer_logs import get_error_logs
    from app.agent.tools.tool_actions.tracer_metrics import get_host_metrics

    return [
        _build_investigation_action(
            name="get_failed_jobs",
            func=get_failed_jobs,
            source="batch",
            requires=["trace_id"],
            availability_check=lambda sources: bool(sources.get("tracer_web", {}).get("trace_id")),
            parameter_extractor=lambda sources: {
                "trace_id": sources.get("tracer_web", {}).get("trace_id")
            },
        ),
        _build_investigation_action(
            name="get_failed_tools",
            func=get_failed_tools,
            source="tracer_web",
            requires=["trace_id"],
            availability_check=lambda sources: bool(sources.get("tracer_web", {}).get("trace_id")),
            parameter_extractor=lambda sources: {
                "trace_id": sources.get("tracer_web", {}).get("trace_id")
            },
        ),
        _build_investigation_action(
            name="get_error_logs",
            func=get_error_logs,
            source="tracer_web",
            requires=["trace_id"],
            availability_check=lambda sources: bool(sources.get("tracer_web", {}).get("trace_id")),
            parameter_extractor=lambda sources: {
                "trace_id": sources.get("tracer_web", {}).get("trace_id"),
                "size": 500,
                "error_only": True,
            },
        ),
        _build_investigation_action(
            name="get_host_metrics",
            func=get_host_metrics,
            source="cloudwatch",
            requires=["trace_id"],
            availability_check=lambda sources: bool(sources.get("tracer_web", {}).get("trace_id")),
            parameter_extractor=lambda sources: {
                "trace_id": sources.get("tracer_web", {}).get("trace_id")
            },
        ),
        _build_investigation_action(
            name="get_cloudwatch_logs",
            func=get_cloudwatch_logs,
            source="cloudwatch",
            requires=[],
            availability_check=lambda sources: bool(
                sources.get("cloudwatch", {}).get("log_group")
                and sources.get("cloudwatch", {}).get("log_stream")
            ),
            parameter_extractor=lambda sources: {
                "log_group": sources.get("cloudwatch", {}).get("log_group"),
                "log_stream": sources.get("cloudwatch", {}).get("log_stream"),
                "limit": 100,
            },
        ),
        _build_investigation_action(
            name="check_s3_marker",
            func=check_s3_marker,
            source="storage",
            requires=[],
            availability_check=lambda sources: bool(
                sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("prefix")
            ),
            parameter_extractor=lambda sources: {
                "bucket": sources.get("s3", {}).get("bucket"),
                "prefix": sources.get("s3", {}).get("prefix"),
            },
        ),
        _build_investigation_action(
            name="inspect_s3_object",
            func=inspect_s3_object,
            source="storage",
            requires=["bucket", "key"],
            availability_check=lambda sources: bool(
                sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("key")
            ),
            parameter_extractor=lambda sources: {
                "bucket": sources.get("s3", {}).get("bucket"),
                "key": sources.get("s3", {}).get("key"),
            },
        ),
        _build_investigation_action(
            name="list_s3_objects",
            func=list_s3_objects,
            source="storage",
            requires=["bucket"],
            availability_check=lambda sources: bool(sources.get("s3", {}).get("bucket")),
            parameter_extractor=lambda sources: {
                "bucket": sources.get("s3", {}).get("bucket"),
                "prefix": sources.get("s3", {}).get("prefix", ""),
                "max_keys": 100,
            },
        ),
        _build_investigation_action(
            name="get_lambda_invocation_logs",
            func=get_lambda_invocation_logs,
            source="cloudwatch",
            requires=["function_name"],
            availability_check=lambda sources: bool(sources.get("lambda", {}).get("function_name")),
            parameter_extractor=lambda sources: {
                "function_name": sources.get("lambda", {}).get("function_name"),
                "filter_errors": False,
                "limit": 50,
            },
        ),
        _build_investigation_action(
            name="get_lambda_errors",
            func=get_lambda_errors,
            source="cloudwatch",
            requires=["function_name"],
            availability_check=lambda sources: bool(sources.get("lambda", {}).get("function_name")),
            parameter_extractor=lambda sources: {
                "function_name": sources.get("lambda", {}).get("function_name"),
                "limit": 50,
            },
        ),
    ]


def get_prioritized_actions(
    sources: list[EvidenceSource] | None = None,
    keywords: list[str] | None = None,
) -> list[InvestigationAction]:
    """
    Get actions prioritized by relevance to sources and keywords.

    Combines source filtering and keyword matching to return a prioritized
    list of actions for the investigation node.

    Args:
        sources: Optional list of evidence sources to filter by
        keywords: Optional keywords to prioritize matching actions

    Returns:
        List of InvestigationAction objects, prioritized by relevance
    """
    all_actions = get_available_actions()

    if not sources and not keywords:
        return all_actions

    # Score each action based on source and keyword matches
    scored_actions: list[tuple[InvestigationAction, int]] = []
    keywords_lower = [kw.lower() for kw in keywords] if keywords else []

    for action in all_actions:
        score = 0

        # Source match gives priority
        if sources and action.source in sources:
            score += 2

        # Keyword match in use cases gives additional priority
        if keywords_lower:
            use_cases_text = " ".join(action.use_cases).lower()
            matching_keywords = sum(1 for kw in keywords_lower if kw in use_cases_text)
            score += matching_keywords

        scored_actions.append((action, score))

    # Sort by score (highest first), then by name for determinism
    scored_actions.sort(key=lambda x: (-x[1], x[0].name))

    return [action for action, _ in scored_actions]
