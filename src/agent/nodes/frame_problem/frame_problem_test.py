import json
import os
from pathlib import Path

import pytest

from src.agent.nodes.frame_problem.extract import extract_alert_details
from src.agent.state import InvestigationState


def test_llm_extracts_alert_details_from_raw_json() -> None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set; skipping LLM integration test.")

    repo_root = Path(__file__).resolve().parents[4]
    fixture_path = repo_root / "tests" / "fixtures" / "grafana_alert.json"
    raw_alert = json.loads(fixture_path.read_text(encoding="utf-8"))
    state: InvestigationState = {"raw_alert": raw_alert}

    details = extract_alert_details(state)

    assert details.affected_table == "events_fact"
    assert details.severity.lower() == "critical"
    assert "freshness" in details.alert_name.lower()
