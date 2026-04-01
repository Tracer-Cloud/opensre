"""
CrashLoopBackOff Demo — genomics alignment pipeline.

Simulates a single Kubernetes pod that is OOMKilled (exit 137) on every
restart attempt, causing BackoffLimitExceeded / CrashLoopBackOff.

No Docker build or local k8s cluster required.  The orchestrator:
  1. Upserts a Datadog log-alert monitor for OOMKilled events.
  2. Ships realistic OOMKilled container logs directly to Datadog Logs Intake
     with full kube-style tags (node_name, node_ip, pod_name, container_name,
     kube_namespace, exit_code).
  3. The monitor fires → Slack alert includes node IP, pod, container, reason.
  4. Tracer RCA agent is triggered → derives affected pod from Datadog evidence
     and produces a root cause report with exact cluster/node/pod location.

Prerequisites:
    DD_API_KEY + DD_APP_KEY in .env

Run with:
    make crashloop-demo
"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()

from pathlib import Path

from tests.e2e.kubernetes.infrastructure_sdk.local import (  # noqa: E402
    create_or_update_monitor,
    load_monitor_definitions,
)
from tests.utils.conftest import get_test_config  # noqa: E402

BASE_DIR = Path(__file__).parent
MONITOR_DEFS = str(BASE_DIR / "datadog-monitor.yaml")

NAMESPACE = "tracer-cl"
CLUSTER = "tracer-cl-demo"
PIPELINE_NAME = "genomics_alignment_pipeline"
NODE_NAME = "desktop-worker"
NODE_IP = "172.22.0.2"
JOB_NAME = "alignment-worker"
CONTAINER_NAME = "align"


# ---------------------------------------------------------------------------
# Datadog helpers
# ---------------------------------------------------------------------------

def _dd(method: str, path: str, body: object = None, *, intake: bool = False) -> dict:
    api_key = os.environ["DD_API_KEY"]
    site = os.environ.get("DD_SITE", "datadoghq.com")
    app_key = os.environ.get("DD_APP_KEY", "")

    host = f"https://http-intake.logs.{site}" if intake else f"https://api.{site}"
    headers: dict[str, str] = {"DD-API-KEY": api_key, "Content-Type": "application/json"}
    if not intake:
        headers["DD-APPLICATION-KEY"] = app_key

    req = urllib.request.Request(
        host + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw) if raw.strip() else {}


def _ship_oomkill_logs(pod_name: str, run_id: str, attempt: int) -> None:
    """Ship OOMKilled container logs for one crash attempt to Datadog Logs Intake.

    JSON attributes (top-level keys) make Datadog template vars like {{@pod_name}}
    resolve in monitor alert messages.  Tags (ddtags) are for search/filtering.
    """
    tags = (
        f"kube_namespace:{NAMESPACE},"
        f"pod_name:{pod_name},"
        f"container_name:{CONTAINER_NAME},"
        f"kube_job:{JOB_NAME},"
        f"cluster:{CLUSTER},"
        f"node_name:{NODE_NAME},"
        f"node_ip:{NODE_IP},"
        f"pipeline:{PIPELINE_NAME},"
        f"run_id:{run_id},"
        f"exit_code:137,"
        f"attempt:{attempt}"
    )

    def _entry(message: str, status: str) -> dict:
        return {
            "ddsource": "kubernetes",
            "ddtags": tags,
            "hostname": NODE_NAME,
            "service": "alignment-pipeline",
            "message": message,
            "status": status,
            # JSON attributes — required for {{@field}} template vars in monitor messages
            "pod_name": pod_name,
            "container_name": CONTAINER_NAME,
            "node_name": NODE_NAME,
            "node_ip": NODE_IP,
            "kube_namespace": NAMESPACE,
            "kube_job": JOB_NAME,
            "cluster": CLUSTER,
            "exit_code": 137,
            "attempt": attempt,
            "run_id": run_id,
        }

    entries = [
        _entry(
            f"[align] Starting alignment worker for run {run_id} (attempt {attempt})",
            "info",
        ),
        _entry(
            "[align] Loading reference genome index GRCh38 into memory (24 GB required)...",
            "info",
        ),
        _entry(
            f"OOMKilled: container {CONTAINER_NAME} in pod {pod_name} on node {NODE_NAME} "
            f"({NODE_IP}) exceeded memory limit. "
            f"Requested=24Gi limit=8Gi. Kernel sent SIGKILL (exit 137). "
            f"run_id={run_id} attempt={attempt}",
            "error",
        ),
        _entry(
            f"[pod-lifecycle] pod={pod_name} job={JOB_NAME} container={CONTAINER_NAME} "
            f"node={NODE_NAME} node_ip={NODE_IP} namespace={NAMESPACE} "
            f"status=OOMKilled exit_code=137 attempt={attempt} run_id={run_id}",
            "error",
        ),
    ]
    _dd("POST", "/api/v2/logs", entries, intake=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    get_test_config()

    if not os.environ.get("DD_API_KEY") or not os.environ.get("DD_APP_KEY"):
        print("DD_API_KEY and DD_APP_KEY must be set in .env")
        return 1

    run_id = f"cl-{uuid.uuid4().hex[:8]}"
    dd_site = os.environ.get("DD_SITE", "datadoghq.com")
    print(f"Run ID: {run_id}  [{datetime.now(UTC).strftime('%H:%M:%S')} UTC]")

    # 1. Upsert monitor
    print("\n[1/3] Upserting Datadog monitor...")
    defs = load_monitor_definitions(MONITOR_DEFS)
    monitor_ids: dict[str, int] = {}
    for d in defs:
        result = create_or_update_monitor(d)
        mid = result.get("id") or result.get("monitor", {}).get("id")
        monitor_ids[d["name"]] = mid
        print(f"  [{mid}] {d['name']}")

    # 2. Ship OOMKilled logs — 3 crash attempts (backoffLimit=2 behaviour)
    print("\n[2/3] Shipping OOMKilled pod logs to Datadog (3 crash attempts)...")
    pod_name = f"{JOB_NAME}-{run_id[:8]}"
    for attempt in range(1, 4):
        _ship_oomkill_logs(pod_name, run_id, attempt)
        print(f"  Attempt {attempt}/3: OOMKilled — pod={pod_name} node={NODE_NAME} ({NODE_IP}) exit=137")

    # 3. Post a summary event
    print("\n[3/3] Posting summary event to Datadog...")
    _dd("POST", "/api/v1/events", {
        "title": f"[tracer-cl] OOMKilled: {JOB_NAME} CrashLoopBackOff ({run_id})",
        "text": (
            f"Run ID: {run_id}\n"
            f"Cluster: {CLUSTER}  Namespace: {NAMESPACE}\n"
            f"Node: {NODE_NAME}  IP: {NODE_IP}\n"
            f"Pod: {pod_name}  Container: {CONTAINER_NAME}\n"
            f"Exit: 137 (OOMKilled) — 3 attempts → BackoffLimitExceeded\n"
            f"Reason: alignment worker exceeded memory limit (8Gi limit, 24Gi requested)\n\n"
            f"Log query: OOMKilled kube_namespace:{NAMESPACE}"
        ),
        "alert_type": "error",
        "priority": "normal",
        "tags": [
            f"cluster:{CLUSTER}",
            f"kube_namespace:{NAMESPACE}",
            f"node_name:{NODE_NAME}",
            f"pod_name:{pod_name}",
            f"pipeline:{PIPELINE_NAME}",
            f"run_id:{run_id}",
            "exit_code:137",
            "reason:OOMKilled",
            "source:tracer-agent",
            "env:local",
        ],
    })

    print("\n" + "=" * 60)
    print("DONE — OOMKilled logs shipped, monitor will fire in ~2 min")
    print("=" * 60)

    q = f"OOMKilled kube_namespace:{NAMESPACE} run_id:{run_id}"
    print(f"\nLogs: https://app.{dd_site}/logs?query={q.replace(' ', '+').replace(':', '%3A')}")

    print("\nMonitors:")
    for _name, mid in monitor_ids.items():
        print(f"  [{mid}] https://app.{dd_site}/monitors/{mid}")

    print("\nExpected Slack alert fields:")
    print(f"  Node:      {NODE_NAME} ({NODE_IP})")
    print(f"  Pod:       {pod_name}")
    print(f"  Container: {CONTAINER_NAME}")
    print(f"  Namespace: {NAMESPACE}")
    print("  Exit:      137 (OOMKilled)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
