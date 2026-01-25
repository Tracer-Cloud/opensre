"""Centralized investigation actions registry with rich metadata."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.agent.state import EvidenceSource


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


def get_available_actions() -> list[InvestigationAction]:
    """
    Get all available investigation actions with rich metadata.

    This provides structured information about what actions are available,
    what they require as input, what they return, and when to use them.
    This helps the LLM make better decisions about which actions to execute.
    """
    from src.agent.tools.tool_actions.tracer_jobs import (
        get_failed_jobs,
        get_failed_tools,
    )
    from src.agent.tools.tool_actions.tracer_logs import get_error_logs
    from src.agent.tools.tool_actions.tracer_metrics import get_host_metrics

    return [
        InvestigationAction(
            name="get_failed_jobs",
            description="Get AWS Batch jobs that failed during execution",
            inputs={
                "trace_id": "The trace/run identifier from tracer_web_run context",
            },
            outputs={
                "failed_jobs": "List of failed job details with job_name, exit_code, status_reason, container_reason",
                "total_jobs": "Total number of jobs in the run",
                "failed_count": "Number of failed jobs",
            },
            use_cases=[
                "Proving job failure hypothesis",
                "Understanding container-level failures",
                "Identifying infrastructure issues",
                "Finding specific job exit codes",
            ],
            requires=["trace_id"],
            source="batch",
            function=get_failed_jobs,
        ),
        InvestigationAction(
            name="get_failed_tools",
            description="Get tools that failed during execution",
            inputs={
                "trace_id": "The trace/run identifier from tracer_web_run context",
            },
            outputs={
                "failed_tools": "List of failed tool details with tool_name, exit_code, reason, explanation",
                "total_tools": "Total number of tools executed",
                "failed_count": "Number of failed tools",
            },
            use_cases=[
                "Proving tool failure hypothesis",
                "Identifying specific failing components",
                "Understanding error patterns",
                "Finding which tools exited with non-zero codes",
            ],
            requires=["trace_id"],
            source="tracer_web",
            function=get_failed_tools,
        ),
        InvestigationAction(
            name="get_error_logs",
            description="Get logs from OpenSearch, optionally filtered for errors",
            inputs={
                "trace_id": "The trace/run identifier from tracer_web_run context",
                "size": "Maximum number of logs to retrieve (default 500)",
                "error_only": "If True, return only error/failure logs (default True)",
            },
            outputs={
                "logs": "List of log entries with message, log_level, timestamp",
                "total_logs": "Total number of logs available",
                "filtered_count": "Number of logs returned after filtering",
            },
            use_cases=[
                "Proving error pattern hypothesis",
                "Finding root cause error messages",
                "Understanding failure timeline",
                "Identifying specific error patterns",
            ],
            requires=["trace_id"],
            source="tracer_web",
            function=get_error_logs,
        ),
        InvestigationAction(
            name="get_host_metrics",
            description="Get host-level metrics (CPU, memory, disk) for the run",
            inputs={
                "trace_id": "The trace/run identifier from tracer_web_run context",
            },
            outputs={
                "metrics": "Validated host metrics with CPU, memory, disk usage",
                "data_quality_issues": "List of any data quality problems found",
            },
            use_cases=[
                "Proving resource constraint hypothesis",
                "Identifying memory/CPU exhaustion",
                "Understanding infrastructure bottlenecks",
                "Checking if resource limits were hit",
            ],
            requires=["trace_id"],
            source="cloudwatch",
            function=get_host_metrics,
        ),
    ]


def get_actions_by_source(source: EvidenceSource) -> list[InvestigationAction]:
    """Get actions filtered by source category."""
    return [action for action in get_available_actions() if action.source == source]


def get_actions_by_use_case(use_case_keywords: list[str]) -> list[InvestigationAction]:
    """Get actions that match use case keywords."""
    keywords_lower = [kw.lower() for kw in use_case_keywords]
    return [
        action
        for action in get_available_actions()
        if any(kw in " ".join(action.use_cases).lower() for kw in keywords_lower)
    ]
