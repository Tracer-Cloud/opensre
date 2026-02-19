"""Format causal chain section for the Slack RCA report.

Included when the dependency context source detected an upstream pipeline failure
before the current investigation pipeline failed.
"""

from __future__ import annotations

from typing import Any


def format_causal_chain_section(dependency_context: dict[str, Any] | None) -> str:
    """Return a formatted Slack section describing the detected causal chain.

    Returns an empty string when no causal chain was detected so callers can
    unconditionally include the return value in the report without extra guards.
    """
    if not dependency_context or not dependency_context.get("causal_chain_detected"):
        return ""

    upstream_pipelines = dependency_context.get("upstream_pipelines", [])
    failed = [
        p for p in upstream_pipelines
        if p.get("status") in ("failed", "error", "Failed", "Error")
    ]
    if not failed:
        return ""

    confidence = dependency_context.get("causal_chain_confidence", 0.85)
    lines = [f"\n*Causal Chain Detected* (confidence {confidence:.0%}):"]

    for p in failed:
        name = p.get("name", "unknown")
        minutes_ago = p.get("minutes_ago")
        shared_asset = p.get("shared_asset", "")

        timing = f"{minutes_ago}min before this pipeline" if minutes_ago else "before this pipeline"
        asset_note = f" via shared asset `{shared_asset}`" if shared_asset else ""

        lines.append(f"• Upstream pipeline `{name}` failed {timing}{asset_note}")

    lines.append("_Upstream failure likely caused downstream data quality issues._\n")
    return "\n".join(lines)
