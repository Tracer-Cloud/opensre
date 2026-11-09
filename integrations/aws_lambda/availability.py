"""Lambda tool availability and function-name extraction.

Shared by the Lambda tools so they do not import private helpers from each
other.
"""

from __future__ import annotations


def lambda_available(sources: dict[str, dict]) -> bool:
    """Return whether a Lambda function name is present in resolved sources."""
    return bool(sources.get("lambda", {}).get("function_name"))


def lambda_name(sources: dict[str, dict]) -> str:
    """Return the Lambda function name from resolved sources, or empty."""
    return str(sources.get("lambda", {}).get("function_name", ""))
