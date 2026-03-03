#!/usr/bin/env python3
"""Local alert simulation test for the Kubernetes PIPELINE_ERROR scenario.

Feeds a real Datadog alert payload directly through the agent pipeline using
pre-built evidence — no live Datadog API calls required. Exercises:
  - node_extract_alert  (LLM: classifies the alert)
  - diagnose_root_cause (LLM: produces root_cause + validated_claims)
  - build_report_context + format_slack_message (report formatting)

Alert used:
  [tracer] Pipeline Error in Logs
  PIPELINE_ERROR: Schema validation failed: Missing fields ['customer_id'] in record 0

Usage (from project root):
    python -m pytest tests/test_case_kubernetes_local_alert_simulation/test_simulation.py -s
    make simulate-k8s-alert
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from app.agent.nodes import node_extract_alert
from app.agent.nodes.publish_findings.formatters.report import format_slack_message
from app.agent.nodes.publish_findings.report_context import build_report_context
from app.agent.nodes.root_cause_diagnosis.node import diagnose_root_cause
from app.agent.state import InvestigationState, make_initial_state

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "datadog_pipeline_error_alert.json"

ERROR_LOG = "PIPELINE_ERROR: Schema validation failed: Missing fields ['customer_id'] in record 0"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def _merge_state(state: InvestigationState, updates: dict[str, Any]) -> None:
    if not updates:
        return
    state_any = cast(dict[str, Any], state)
    for key, value in updates.items():
        state_any[key] = value


def test_kubernetes_local_alert_simulation() -> None:
    """Feed the Datadog pipeline-error alert through the agent and verify the report.

    Skips live Datadog API calls by injecting pre-built evidence directly into state.
    Asserts:
      - root_cause is non-empty and references the missing field
      - the report contains the exact error log line as a code block
      - the log appears directly below the Root Cause heading
    """
    fixture = _load_fixture()

    state = make_initial_state(
        alert_name=fixture["alert"]["title"],
        pipeline_name="tracer-test",
        severity="critical",
        raw_alert=fixture["alert"],
    )
    _merge_state(state, node_extract_alert(state))

    cast(dict[str, Any], state)["evidence"] = fixture["evidence"]

    result = diagnose_root_cause(state)
    _merge_state(state, result)

    ctx = build_report_context(state)
    report = format_slack_message(ctx)

    print("\n" + "=" * 70)
    print("SIMULATION REPORT OUTPUT")
    print("=" * 70)
    print(report)
    print("=" * 70)

    assert result["root_cause"], "root_cause must be non-empty"
    assert "customer_id" in result["root_cause"].lower(), (
        f"root_cause should reference 'customer_id', got: {result['root_cause']}"
    )

    assert f"`{ERROR_LOG}`" in report, (
        f"Report must contain the error log as a code block.\n"
        f"Expected: `{ERROR_LOG}`\n"
        f"Report:\n{report}"
    )

    rc_idx = report.find("*Root Cause*") if "*Root Cause*" in report else report.find("*Root Cause:*")
    log_idx = report.find(f"`{ERROR_LOG}`")
    assert rc_idx != -1, "Report must contain a Root Cause section"
    assert log_idx > rc_idx, "Log code block must appear after the Root Cause heading"

    findings_idx = report.find("*Findings*") if "*Findings*" in report else report.find("*Validated Claims")
    if findings_idx != -1:
        assert log_idx < findings_idx, "Log code block must appear before Findings section"

    print(f"\nPASS: root_cause_category={result.get('root_cause_category')}")
    print(f"Root cause: {result['root_cause']}")
