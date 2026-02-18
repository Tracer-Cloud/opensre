#!/usr/bin/env python3
"""
Kubernetes + Datadog integration test.

Deploys a Datadog Agent in a local kind cluster, runs the ETL job,
and verifies logs arrive in Datadog.

Prerequisites:
    brew install kind kubectl
    Docker Desktop running
    DD_API_KEY environment variable set
    DD_APP_KEY environment variable set (for log query verification)
    DD_SITE environment variable set (optional, defaults to datadoghq.com)

Usage (from project root):
    python -m tests.test_case_kubernetes.test_datadog
    python -m tests.test_case_kubernetes.test_datadog --keep-cluster
    python -m tests.test_case_kubernetes.test_datadog --skip-verify  # skip Datadog API check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

from tests.test_case_kubernetes.infrastructure_sdk.local import (
    apply_manifest,
    build_image,
    check_prerequisites,
    create_kind_cluster,
    delete_kind_cluster,
    delete_manifest,
    deploy_datadog_agent,
    get_pod_logs,
    load_image,
    wait_for_datadog_agent,
    wait_for_job,
)

CLUSTER_NAME = "tracer-k8s-test"
IMAGE_TAG = "tracer-k8s-test:latest"
NAMESPACE = "tracer-test"

BASE_DIR = os.path.dirname(__file__)
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline_code")
MANIFESTS_DIR = os.path.join(BASE_DIR, "k8s_manifests")

NAMESPACE_MANIFEST = os.path.join(MANIFESTS_DIR, "namespace.yaml")
JOB_ERROR_MANIFEST = os.path.join(MANIFESTS_DIR, "job-with-error.yaml")


def query_datadog_logs(query: str, from_seconds_ago: int = 300) -> list[dict]:
    """Query Datadog Logs API. Returns list of log entries."""
    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    site = os.environ.get("DD_SITE", "datadoghq.com")

    if not api_key or not app_key:
        print("DD_API_KEY and DD_APP_KEY required for log verification")
        return []

    payload = json.dumps({
        "filter": {
            "query": query,
            "from": f"now-{from_seconds_ago}s",
            "to": "now",
        },
        "sort": "-timestamp",
        "page": {"limit": 10},
    }).encode()

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
    parser.add_argument("--keep-cluster", action="store_true", help="Don't delete kind cluster after test")
    parser.add_argument("--skip-verify", action="store_true", help="Skip Datadog API log verification")
    args = parser.parse_args()

    missing = check_prerequisites()
    if missing:
        print(f"Missing prerequisites: {', '.join(missing)}")
        return 1

    if not os.environ.get("DD_API_KEY"):
        print("DD_API_KEY environment variable is required")
        return 1

    passed = True
    try:
        # 1. Setup cluster + Datadog Agent
        create_kind_cluster(CLUSTER_NAME)
        build_image(PIPELINE_DIR, IMAGE_TAG)
        load_image(CLUSTER_NAME, IMAGE_TAG)
        apply_manifest(NAMESPACE_MANIFEST)
        deploy_datadog_agent(MANIFESTS_DIR, NAMESPACE)

        if not wait_for_datadog_agent(NAMESPACE):
            print("FAIL: Datadog Agent did not become ready")
            return 1

        # 2. Run error job (produces distinctive log output)
        print("\n--- Running error job ---")
        apply_manifest(JOB_ERROR_MANIFEST)
        status = wait_for_job(NAMESPACE, "simple-etl-error")
        logs = get_pod_logs(NAMESPACE, "app=simple-etl-error")
        print(f"Job status: {status}")
        print(f"Pod logs:\n{logs}")

        if status != "failed":
            print("FAIL: job should have failed")
            passed = False

        if "Injected ETL failure" not in logs:
            print("FAIL: expected error not in pod logs")
            passed = False

        # 3. Give Datadog Agent time to ship the logs
        if not args.skip_verify and passed:
            print("\nWaiting 30s for Datadog Agent to flush logs...")
            time.sleep(30)

            if not verify_logs_in_datadog():
                passed = False

        delete_manifest(JOB_ERROR_MANIFEST)
    finally:
        if not args.keep_cluster:
            delete_kind_cluster(CLUSTER_NAME)

    status_text = "PASSED" if passed else "FAILED"
    print(f"\n{'=' * 60}")
    print(f"TEST {status_text}")
    print(f"{'=' * 60}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
