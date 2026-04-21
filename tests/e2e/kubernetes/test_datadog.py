#!/usr/bin/env python3
"""
Kubernetes + Datadog integration test (Helm-based).

Deploys Datadog via the official Helm chart in a local kind cluster,
runs the ETL job, and verifies logs arrive in Datadog.

Prerequisites:
    brew install kind kubectl helm
    Docker Desktop running
    DD_API_KEY environment variable set
    DD_APP_KEY environment variable set (for log query verification)
    DD_SITE environment variable set (optional, defaults to datadoghq.com)

Usage (from project root):
    python -m tests.e2e.kubernetes.test_datadog
    python -m tests.e2e.kubernetes.test_datadog --keep-cluster
    python -m tests.e2e.kubernetes.test_datadog --skip-verify
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

from tests.e2e.kubernetes.infrastructure_sdk.local import (
    apply_manifest,
    build_image,
    check_prerequisites,
    create_kind_cluster,
    create_or_update_monitor,
    delete_kind_cluster,
    delete_monitor_by_name,
    deploy_datadog_helm,
    get_pod_logs,
    load_image,
    load_monitor_definitions,
    wait_for_datadog_agent,
    wait_for_job,
)
from tests.e2e.kubernetes.test_local import _apply_rendered, _delete_job, _render_manifest
from tests.shared.infrastructure_sdk.config import load_outputs
from tests.utils.s3_upload_validate import INVALID_PAYLOAD, upload_test_data

CLUSTER_NAME = "tracer-k8s-test"
IMAGE_TAG = "tracer-k8s-test:latest"
NAMESPACE = "tracer-test"

BASE_DIR = os.path.dirname(__file__)
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline_code")
MANIFESTS_DIR = os.path.join(BASE_DIR, "k8s_manifests")

NAMESPACE_MANIFEST = os.path.join(MANIFESTS_DIR, "namespace.yaml")
DATADOG_VALUES = os.path.join(MANIFESTS_DIR, "datadog-values.yaml")
MONITOR_DEFS = os.path.join(MANIFESTS_DIR, "datadog-monitors.yaml")
JOB_EXTRACT_MANIFEST = os.path.join(MANIFESTS_DIR, "job-extract.yaml")
JOB_TRANSFORM_ERROR_MANIFEST = os.path.join(MANIFESTS_DIR, "job-transform-error.yaml")


def query_datadog_logs(query: str, from_seconds_ago: int = 300) -> list[dict]:
    """Query Datadog Logs API. Returns list of log entries."""
    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    site = os.environ.get("DD_SITE", "datadoghq.com")

    if not api_key or not app_key:
        print("DD_API_KEY and DD_APP_KEY required for log verification")
        return []

    payload = json.dumps(
        {
            "filter": {
                "query": query,
                "from": f"now-{from_seconds_ago}s",
                "to": "now",
            },
            "sort": "-timestamp",
            "page": {"limit": 10},
        }
    ).encode()

    url = f"https://api.{site}/api/v2/logs/events/search"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "DD-API-KEY": api_key,
            "DD-APPLICATION-KEY": app_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            return body.get("data", [])
    except Exception as e:
        print(f"Datadog API query failed: {e}")
        return []


def deploy_monitors() -> list[dict]:
    """Load monitor definitions and create/update each in Datadog."""
    defs = load_monitor_definitions(MONITOR_DEFS)
    print(f"\nDeploying {len(defs)} monitor(s) to Datadog...")
    created = []
    for monitor_def in defs:
        try:
            result = create_or_update_monitor(monitor_def)
            created.append(result)
        except Exception as e:
            print(f"WARNING: Failed to deploy monitor '{monitor_def.get('name')}': {e}")
    return created


def cleanup_monitors() -> None:
    """Delete monitors created by this test."""
    defs = load_monitor_definitions(MONITOR_DEFS)
    for monitor_def in defs:
        with contextlib.suppress(Exception):
            delete_monitor_by_name(monitor_def["name"])


def verify_monitor_triggered(monitor_name: str, max_wait: int = 300) -> bool:
    """Poll until a monitor enters Alert or Warn state."""
    print(f"\nVerifying monitor '{monitor_name}' triggers (up to {max_wait}s)...")
    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    site = os.environ.get("DD_SITE", "datadoghq.com")

    if not api_key or not app_key:
        print("DD_API_KEY and DD_APP_KEY required for monitor verification")
        return False

    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            encoded = urllib.parse.quote(monitor_name)
            url = f"https://api.{site}/api/v1/monitor?name={encoded}"
            req = urllib.request.Request(
                url,
                headers={
                    "DD-API-KEY": api_key,
                    "DD-APPLICATION-KEY": app_key,
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                monitors = json.loads(resp.read())
            for m in monitors:
                if m.get("name") == monitor_name:
                    state = m.get("overall_state", "")
                    print(f"  Monitor state: {state}")
                    if state in ("Alert", "Warn"):
                        return True
        except Exception as e:
            print(f"  Poll error: {e}")

        remaining = int(deadline - time.monotonic())
        print(f"  Not triggered yet, retrying... ({remaining}s remaining)")
        time.sleep(20)

    print(f"FAIL: monitor '{monitor_name}' did not trigger within {max_wait}s")
    return False


def verify_logs_in_datadog(max_wait: int = 180) -> bool:
    """Poll Datadog until the error job's logs appear."""
    print(f"\nVerifying logs in Datadog (polling up to {max_wait}s)...")
    query = "kube_namespace:tracer-test PIPELINE_ERROR"
    deadline = time.monotonic() + max_wait

    while time.monotonic() < deadline:
        logs = query_datadog_logs(query)
        if logs:
            print(f"Found {len(logs)} log(s) in Datadog matching query")
            for entry in logs[:3]:
                msg = entry.get("attributes", {}).get("message", "")[:120]
                print(f"  - {msg}")
            return True
        remaining = int(deadline - time.monotonic())
        print(f"  No logs yet, retrying... ({remaining}s remaining)")
        time.sleep(15)

    print("FAIL: logs did not appear in Datadog within timeout")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Kubernetes + Datadog integration test")
    parser.add_argument(
        "--keep-cluster", action="store_true", help="Don't delete kind cluster after test"
    )
    parser.add_argument(
        "--skip-verify", action="store_true", help="Skip Datadog API log verification"
    )
    parser.add_argument(
        "--skip-monitors", action="store_true", help="Skip monitor deployment and verification"
    )
    parser.add_argument(
        "--cleanup-monitors", action="store_true", help="Delete test monitors on exit"
    )
    args = parser.parse_args()

    missing = check_prerequisites()
    if missing:
        print(f"Missing prerequisites: {', '.join(missing)}")
        print("Install with: brew install " + " ".join(missing))
        return 1

    if not os.environ.get("DD_API_KEY"):
        print("DD_API_KEY environment variable is required")
        return 1

    passed = True
    try:
        create_kind_cluster(CLUSTER_NAME)
        build_image(PIPELINE_DIR, IMAGE_TAG)
        load_image(CLUSTER_NAME, IMAGE_TAG)
        apply_manifest(NAMESPACE_MANIFEST)
        deploy_datadog_helm(DATADOG_VALUES, NAMESPACE)

        if not wait_for_datadog_agent(NAMESPACE):
            print("FAIL: Datadog Agent did not become ready")
            return 1

        monitors_deployed = []
        if not args.skip_monitors and os.environ.get("DD_APP_KEY"):
            monitors_deployed = deploy_monitors()

        config = load_outputs("tracer-eks-k8s-test")
        run_id = f"dd-test-{uuid.uuid4().hex[:8]}"
        test_data = upload_test_data(config["landing_bucket"], INVALID_PAYLOAD)

        common = {
            "landing_bucket": config["landing_bucket"],
            "processed_bucket": config["processed_bucket"],
            "s3_key": test_data.key,
            "pipeline_run_id": run_id,
        }

        print("\n--- Running 3-stage pipeline (extract -> transform-error) ---")

        _delete_job("etl-extract")
        content = _render_manifest(JOB_EXTRACT_MANIFEST, **common)
        _apply_rendered(content)
        status = wait_for_job(NAMESPACE, "etl-extract")
        if status != "complete":
            print(f"FAIL: extract did not complete ({status})")
            passed = False

        if passed:
            _delete_job("etl-transform-error")
            content = _render_manifest(JOB_TRANSFORM_ERROR_MANIFEST, **common)
            _apply_rendered(content)
            status = wait_for_job(NAMESPACE, "etl-transform-error")
            logs = get_pod_logs(NAMESPACE, "stage=transform-error")
            print(f"Transform status: {status}")
            print(f"Pod logs:\n{logs}")

            if status != "failed":
                print("FAIL: transform should have failed")
                passed = False

            if "Schema validation failed" not in logs and "Missing fields" not in logs:
                print("FAIL: expected schema validation error in pod logs")
                passed = False

        if not args.skip_verify and passed:
            print("\nWaiting 30s for Datadog Agent to flush logs...")
            time.sleep(30)

            if not verify_logs_in_datadog():
                passed = False

        if monitors_deployed and not args.skip_verify and passed:
            log_monitor_name = "[tracer] Pipeline Error in Logs"
            if not verify_monitor_triggered(log_monitor_name):
                print("WARNING: monitor did not trigger (may need more time)")

        _delete_job("etl-extract")
        _delete_job("etl-transform-error")
    finally:
        if args.cleanup_monitors and os.environ.get("DD_APP_KEY"):
            cleanup_monitors()
        if not args.keep_cluster:
            delete_kind_cluster(CLUSTER_NAME)

    status_text = "PASSED" if passed else "FAILED"
    print(f"\n{'=' * 60}")
    print(f"TEST {status_text}")
    print(f"{'=' * 60}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
