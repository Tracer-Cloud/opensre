import json
import os
from pathlib import Path

from src.agent.nodes.extract_alert.extract import extract_alert_details
from src.agent.state import InvestigationState


def test_llm_extracts_alert_details_from_raw_json() -> None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    assert api_key, "ANTHROPIC_API_KEY must be set for this integration test"

    # Find repo root by looking for pytest.ini (more reliable than counting parents)
    test_file = Path(__file__).resolve()
    current = test_file.parent
    while current != current.parent:  # Stop at filesystem root
        if (current / "pytest.ini").exists():
            repo_root = current
            break
        current = current.parent
    else:
        # Fallback: use parents[4] if pytest.ini not found
        repo_root = test_file.parents[4]

    fixture_path = repo_root / "tests" / "fixtures" / "grafana_alert.json"
    raw_alert = json.loads(fixture_path.read_text(encoding="utf-8"))
    state: InvestigationState = {"raw_alert": raw_alert}

    details = extract_alert_details(state)

    # The fixture contains superfluid_prod_pipeline, not events_fact
    assert details.affected_table == "superfluid_prod_pipeline"
    assert details.severity.lower() == "critical"
    assert "pipeline" in details.alert_name.lower() or "failure" in details.alert_name.lower()
