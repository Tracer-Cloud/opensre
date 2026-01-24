"""Generate investigation hypotheses based on alert type."""

from src.agent.state import EvidenceSource, InvestigationState

# Alert patterns -> evidence sources to check
ALERT_RULES: dict[str, list[EvidenceSource]] = {
    "freshness": ["tracer", "storage", "batch"],
    "sla": ["tracer", "storage", "batch"],
    "pipeline": ["tracer", "batch"],
    "job": ["tracer", "batch"],
    "failed": ["tracer", "batch"],
    "missing": ["storage", "tracer"],
    "storage": ["storage", "tracer"],
    "s3": ["storage", "tracer"],
}

DEFAULT_SOURCES: list[EvidenceSource] = ["tracer", "storage", "batch"]


def node_generate_hypotheses(state: InvestigationState) -> dict:
    """Generate plan_sources based on alert type using simple rules."""
    alert = state.get("alert_name", "").lower()
    table = state.get("affected_table", "").lower()

    # Match first rule that applies
    for pattern, sources in ALERT_RULES.items():
        if pattern in alert:
            return {"plan_sources": sources}

    # Table-specific fallback
    if "events" in table or "fact" in table:
        return {"plan_sources": ["tracer", "storage", "batch"]}

    return {"plan_sources": DEFAULT_SOURCES}

