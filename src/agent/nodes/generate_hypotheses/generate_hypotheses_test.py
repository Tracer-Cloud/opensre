import os

from dotenv import load_dotenv

from src.agent.nodes.generate_hypotheses.generate_hypotheses import node_generate_hypotheses
from src.agent.state import EvidenceSource, InvestigationState


def test_generate_hypotheses_produces_plan_sources() -> None:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    assert api_key, "ANTHROPIC_API_KEY must be set for this integration test"

    state: InvestigationState = {
        "alert_name": "DataFreshnessSLABreach",
        "affected_table": "events_fact",
        "severity": "critical",
        "problem_md": "Freshness SLA breached for events_fact table.",
    }

    result = node_generate_hypotheses(state)
    plan_sources = result.get("plan_sources", [])

    assert plan_sources, "plan_sources should not be empty"
    assert "tracer_web" in plan_sources
    for source in plan_sources:
        assert source in EvidenceSource.__args__
