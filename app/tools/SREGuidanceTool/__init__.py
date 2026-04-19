"""SRE knowledge retrieval tool for pipeline incident investigation."""

from __future__ import annotations

from typing import Any

from app.tools.SREGuidanceTool.knowledge_base import (
    get_sre_guidance as _get_sre_guidance,
)
from app.tools.SREGuidanceTool.knowledge_base import (
    get_topics_for_keywords,
)
from app.tools.tool_decorator import tool


def _extract_guidance_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"keywords": sources.get("problem_keywords", [])}


@tool(
    name="get_sre_guidance",
    source="knowledge",
    description="Retrieve SRE best practices for data pipeline incidents.",
    use_cases=[
        "Understanding pipeline failure patterns (delayed data, corrupt data)",
        "Applying SLO concepts to data freshness and correctness issues",
        "Identifying hotspotting and resource contention patterns",
        "Getting remediation guidance for common pipeline failures",
        "Structuring postmortem findings and recommendations",
    ],
    tags=("safe", "fast", "no-credentials"),
    cost_tier="cheap",
    input_schema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Specific topic: pipeline_types, slo_freshness, slo_correctness, failure_delayed_data, failure_corrupt_data, hotspotting, thundering_herd, monitoring_pipelines, dependency_failure, recovery_remediation, resource_planning, pipeline_documentation, workflow_patterns",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keywords to match against SRE content (e.g., ['timeout', 'delay'])",
            },
            "max_topics": {"type": "integer", "default": 3},
        },
        "required": [],
    },
    extract_params=_extract_guidance_params,
)
def get_sre_guidance(
    topic: str | None = None,
    keywords: list[str] | None = None,
    max_topics: int = 3,
) -> dict[str, Any]:
    """Retrieve SRE best practices for data pipeline incidents."""
    return _get_sre_guidance(topic=topic, keywords=keywords, max_topics=max_topics)


__all__ = ["get_sre_guidance", "get_topics_for_keywords"]
