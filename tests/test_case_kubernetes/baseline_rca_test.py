#!/usr/bin/env python3
"""Baseline RCA test for the Kubernetes etl-transform job failure.

Runs the CURRENT, UNMODIFIED pipeline on the Datadog fixture and records
the output. No accuracy assertions -- this establishes the "before" snapshot
so Phase 2 changes have a concrete comparison point.

Usage (from project root):
    python -m pytest tests/test_case_kubernetes/baseline_rca_test.py -s
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


def test_baseline_kubernetes_rca():
    """Run the current pipeline on the K8s fixture and record output.

    This is NOT an accuracy test. It records the current state so Phase 2
    has a concrete before/after comparison.
    """
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

    print("\n" + "=" * 60)
    print("BASELINE RCA OUTPUT (current pipeline, no fixes applied)")
    print("=" * 60)
    print(f"\nRoot cause:\n  {result['root_cause']}")
    print(f"\nValidated claims ({len(result.get('validated_claims', []))}):")
    for c in result.get("validated_claims", []):
        claim_text = c.get("claim", c) if isinstance(c, dict) else c
        print(f"  - {claim_text}")
    print(f"\nNon-validated claims ({len(result.get('non_validated_claims', []))}):")
    for c in result.get("non_validated_claims", []):
        claim_text = c.get("claim", c) if isinstance(c, dict) else c
        print(f"  - {claim_text}")

    evidence = fixture["evidence"]
    print("\nEvidence available:")
    print(f"  Datadog logs: {len(evidence.get('datadog_logs', []))}")
    print(f"  Datadog error logs: {len(evidence.get('datadog_error_logs', []))}")
    print(f"  Datadog monitors: {len(evidence.get('datadog_monitors', []))}")

    has_k8s_tags = any(
        any(t.startswith("kube_") for t in log.get("tags", []) if isinstance(t, str))
        for log in evidence.get("datadog_logs", [])
    )
    print(f"  K8s tags present in logs: {has_k8s_tags}")
    print("  K8s tags surfaced to LLM: False (not yet implemented)")
    print("=" * 60)

    assert result["root_cause"], "Pipeline should produce a non-empty root cause"
