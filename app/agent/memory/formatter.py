"""Memory file content formatting."""

from datetime import datetime


def format_memory_content(
    timestamp: datetime,
    pipeline_name: str,
    alert_id_short: str,
    confidence: float,
    validity_score: float,
    problem_pattern: str | None = None,
    action_sequence: list[str] | None = None,
    root_cause: str | None = None,
    data_lineage: str | None = None,
    rca_report: str | None = None,
) -> str:
    """
    Format investigation data into structured markdown.

    Follows Openclaw session-memory pattern.

    Args:
        timestamp: Investigation timestamp
        pipeline_name: Pipeline name
        alert_id_short: Short alert ID (8 chars)
        confidence: Investigation confidence score
        validity_score: Claim validity score
        problem_pattern: Problem statement pattern
        action_sequence: Successful action sequence
        root_cause: Root cause summary
        data_lineage: Data lineage nodes
        rca_report: Full RCA report

    Returns:
        Formatted markdown content
    """
    content_parts = [
        f"# Session: {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- **Pipeline**: {pipeline_name}",
        f"- **Alert ID**: {alert_id_short}",
        f"- **Confidence**: {confidence:.0%}",
        f"- **Validity**: {validity_score:.0%}",
        "",
    ]

    if problem_pattern:
        content_parts.extend(["## Problem Pattern", problem_pattern, ""])

    if action_sequence:
        content_parts.extend(
            [
                "## Investigation Path",
                "\n".join(f"{i}. {action}" for i, action in enumerate(action_sequence, 1)),
                "",
            ]
        )

    if root_cause:
        content_parts.extend(["## Root Cause", root_cause, ""])

    if data_lineage:
        content_parts.extend(["## Data Lineage", data_lineage, ""])

    # Include full RCA report for complete context
    if rca_report:
        content_parts.extend(["## Full RCA Report", "", rca_report, ""])

    return "\n".join(content_parts)


def generate_memory_filename(pipeline_name: str, alert_id: str, timestamp: datetime) -> str:
    """
    Generate deterministic memory filename (Openclaw pattern).

    Format: YYYY-MM-DD-<pipeline_name>-<alert_id8>.md

    Args:
        pipeline_name: Pipeline name
        alert_id: Full alert ID
        timestamp: Investigation timestamp

    Returns:
        Filename string
    """
    date_str = timestamp.strftime("%Y-%m-%d")
    alert_id_short = alert_id[:8] if alert_id else "unknown"
    return f"{date_str}-{pipeline_name}-{alert_id_short}.md"
