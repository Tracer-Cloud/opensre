#!/usr/bin/env python3
"""Accurate RCA test for the Kubernetes etl-transform job failure.

Asserts that the fixed pipeline correctly identifies the root cause category
as 'configuration_error' for a schema validation failure caused by a
misconfigured REQUIRED_FIELDS environment variable in the K8s Job manifest.

Usage (from project root):
    python -m pytest tests/test_case_kubernetes/accurate_rca_test.py -s
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from app.agent.nodes import node_extract_alert
from app.agent.nodes.root_cause_diagnosis.node import diagnose_root_cause
from app.agent.state import InvestigationState, make_initial_state

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "datadog_k8s_alert.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def _merge_state(state: InvestigationState, updates: dict[str, Any]) -> None:
    if not updates:
        return
    state_any = cast(dict[str, Any], state)
    for key, value in updates.items():
        state_any[key] = value


def test_accurate_kubernetes_rca():
    """Assert the fixed pipeline produces an accurate RCA for the K8s scenario."""
    fixture = _load_fixture()

    state = make_initial_state(
        alert_name=fixture["alert"]["title"],
        pipeline_name="kubernetes_etl_pipeline",
        severity="critical",
        raw_alert=fixture["alert"],
    )
    _merge_state(state, node_extract_alert(state))

    state_any = cast(dict[str, Any], state)
    state_any["evidence"] = fixture["evidence"]

    result = diagnose_root_cause(state)

    assert result["root_cause_category"] == "configuration_error", (
        f"Expected root_cause_category='configuration_error', "
        f"got '{result['root_cause_category']}'\n"
        f"Full root cause: {result['root_cause']}"
    )

    for claim in result.get("validated_claims", []):
        claim_text = claim.get("claim", "") if isinstance(claim, dict) else str(claim)
        assert not claim_text.startswith("NON_"), (
            f"Parsing artifact in validated claims: '{claim_text}'"
        )

    causal_chain = result.get("causal_chain", [])
    assert len(causal_chain) >= 2, (
        f"Causal chain should have at least 2 steps, got {len(causal_chain)}: {causal_chain}"
    )

    print(f"\nPASS: root_cause_category={result['root_cause_category']}")
    print(f"Root cause: {result['root_cause']}")
    print(f"Causal chain ({len(causal_chain)} steps):")
    for step in causal_chain:
        print(f"  -> {step}")
