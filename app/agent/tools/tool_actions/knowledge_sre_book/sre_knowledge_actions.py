"""SRE knowledge retrieval tool action for pipeline incident investigation."""

from app.agent.tools.tool_actions.knowledge_sre_book.sre_knowledge_base import (
    get_sre_guidance as _get_sre_guidance,
)
from app.agent.tools.tool_actions.knowledge_sre_book.sre_knowledge_base import (
    get_topics_for_keywords,
)


def get_sre_guidance(
    topic: str | None = None,
    keywords: list[str] | None = None,
    max_topics: int = 3,
) -> dict:
    """Retrieve SRE best practices for data pipeline incidents.

    Provides guidance from Google's SRE Book and Workbook chapters on
    Data Processing Pipelines. Use this to understand failure patterns,
    apply SLO concepts, and structure remediation recommendations.

    Useful for:
    - Understanding pipeline failure patterns (delayed data, corrupt data)
    - Applying SLO concepts to data freshness and correctness issues
    - Identifying hotspotting and resource contention patterns
    - Getting remediation guidance for common pipeline failures
    - Structuring postmortem findings and recommendations

    Args:
        topic: Specific topic to retrieve. Available topics:
               - pipeline_types: ETL, ML, Analytics pipeline classifications
               - slo_freshness: Data freshness SLO patterns
               - slo_correctness: Data correctness and skewness SLOs
               - failure_delayed_data: Delayed data failure modes
               - failure_corrupt_data: Corrupt data failure modes
               - hotspotting: Resource bottlenecks and load patterns
               - thundering_herd: Concurrent access and retry storms
               - monitoring_pipelines: Pipeline observability practices
               - dependency_failure: Handling upstream/downstream failures
               - recovery_remediation: Recovery and rollback strategies
               - resource_planning: Autoscaling and capacity planning
               - pipeline_documentation: Runbooks and system diagrams
               - workflow_patterns: Continuous pipeline architectures
        keywords: Keywords to match against SRE content (e.g., ["timeout", "delay"])
        max_topics: Maximum number of topics to return when using keywords

    Returns:
        Dictionary with:
        - success: Whether guidance was found
        - topics: List of matched topic names
        - guidance: List of dicts with topic name, content, and source
        - sources: List of source references
    """
    return _get_sre_guidance(topic=topic, keywords=keywords, max_topics=max_topics)


__all__ = ["get_sre_guidance", "get_topics_for_keywords"]
