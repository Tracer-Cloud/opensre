#!/usr/bin/env python3
"""Local alert simulation test for the Kubernetes PIPELINE_ERROR scenario.

POSTs a real Datadog alert payload to a locally running LangGraph dev server
and runs the full investigation pipeline (including live Datadog API calls).

Alert used:
  [tracer] Pipeline Error in Logs
  PIPELINE_ERROR: Schema validation failed: Missing fields ['customer_id'] in record 0

Prerequisites:
  The LangGraph server must be running on localhost:2024 before this test is
  invoked. `make simulate-k8s-alert` handles that automatically.

Usage (from project root):
    make simulate-k8s-alert
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY - run manually",
)

BASE_URL = "http://localhost:2024"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "datadog_pipeline_error_alert.json"
RUN_TIMEOUT_SECONDS = 120
POLL_INTERVAL = 2


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("JWT_TOKEN", "")
    if not token:
        raise RuntimeError("JWT_TOKEN env var is required but not set")
    return {"Authorization": f"Bearer {token}"}


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", **_auth_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _get(path: str) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers=_auth_headers(),
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _wait_for_run(thread_id: str, run_id: str) -> dict:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        run = _get(f"/threads/{thread_id}/runs/{run_id}")
        status = run.get("status")
        if status in ("success", "error"):
            return run
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Run {run_id} did not complete within {RUN_TIMEOUT_SECONDS}s")


def test_kubernetes_local_alert_simulation() -> None:
    """POST a pipeline-error alert to the local LangGraph server and verify the report.

    Runs the full investigation pipeline. Asserts:
      - root_cause is non-empty and references the missing field
      - slack_message is non-empty and contains a Root Cause section
    """
    fixture = _load_fixture()
    alert = fixture["alert"]

    thread = _post("/threads", {})
    thread_id = thread["thread_id"]

    run = _post(
        f"/threads/{thread_id}/runs",
        {
            "assistant_id": "agent",
            "input": {
                "mode": "investigation",
                "alert_name": alert["title"],
                "pipeline_name": alert["commonLabels"].get("pipeline_name", "tracer-test"),
                "severity": alert["commonLabels"].get("severity", "critical"),
                "raw_alert": alert,
            },
        },
    )
    run_id = run["run_id"]

    completed = _wait_for_run(thread_id, run_id)
    assert completed["status"] == "success", f"Run ended with status={completed['status']}"

    state = _get(f"/threads/{thread_id}/state")
    values = state.get("values", {})

    root_cause = values.get("root_cause", "")
    slack_message = values.get("slack_message", "")

    print("\n" + "=" * 70)
    print("SIMULATION REPORT OUTPUT")
    print("=" * 70)
    print(slack_message)
    print("=" * 70)
    print(f"\nroot_cause: {root_cause}")

    assert root_cause, "root_cause must be non-empty"
    assert "customer_id" in root_cause.lower(), (
        f"root_cause should reference 'customer_id', got: {root_cause}"
    )

    assert slack_message, "slack_message must be non-empty"
    assert "Root Cause" in slack_message, (
        f"slack_message must contain a Root Cause section.\nGot:\n{slack_message}"
    )
