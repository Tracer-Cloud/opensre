"""Keyword extraction for action prioritization.

Extracts relevant keywords from problem statements and alert names
to help prioritize investigation actions.
"""

# Keywords that indicate specific investigation needs
KEYWORD_PATTERNS = [
    "memory",
    "oom",
    "killed",
    "timeout",
    "slow",
    "hang",
    "failure",
    "failed",
    "error",
    "exception",
    "crash",
    "batch",
    "job",
    "task",
    "tool",
    "pipeline",
    "log",
    "logs",
    "trace",
    "debug",
    "metrics",
    "rds",
    "postgres",
    "database",
    "replication",
    "connections",
    "storage",
    "failover",
    "cpu",
    "disk",
    "resource",
]


def extract_keywords(problem_md: str, alert_name: str) -> list[str]:
    """
    Extract relevant keywords from problem statement and alert name.

    Combines the problem statement markdown and alert name, converts to lowercase,
    and returns a list of keyword patterns that appear in the combined text.

    Args:
        problem_md: Problem statement markdown (may be empty)
        alert_name: Alert name (may be empty)

    Returns:
        List of matching keyword patterns (empty list if no matches)

    Examples:
        >>> extract_keywords("Pipeline failed with memory error", "PipelineFailure")
        ['failure', 'failed', 'error', 'pipeline', 'memory']
        >>> extract_keywords("", "BatchJobTimeout")
        ['batch', 'job', 'timeout']
        >>> extract_keywords("No issues detected", "Success")
        []
    """
    text = f"{problem_md} {alert_name}".lower()
    return [kw for kw in KEYWORD_PATTERNS if kw in text]
