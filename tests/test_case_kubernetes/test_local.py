#!/usr/bin/env python3
"""
Local Kubernetes test for minimal ETL job on kind.

Prerequisites:
    brew install kind kubectl
    Docker Desktop running

Usage (from project root):
    python -m tests.test_case_kubernetes.test_local             # Run both tests
    python -m tests.test_case_kubernetes.test_local --success   # Success path only
    python -m tests.test_case_kubernetes.test_local --fail      # Failure path only
    python -m tests.test_case_kubernetes.test_local --keep-cluster  # Don't delete cluster after
"""

from __future__ import annotations

import argparse
import os
import sys

from tests.test_case_kubernetes.infrastructure_sdk.local import (
    apply_manifest,
    build_image,
    check_prerequisites_basic,
    create_kind_cluster,
    delete_kind_cluster,
    delete_manifest,
    get_pod_logs,
    load_image,
    wait_for_job,
)

CLUSTER_NAME = "tracer-k8s-test"
IMAGE_TAG = "tracer-k8s-test:latest"

BASE_DIR = os.path.dirname(__file__)
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline_code")
MANIFESTS_DIR = os.path.join(BASE_DIR, "k8s_manifests")

NAMESPACE_MANIFEST = os.path.join(MANIFESTS_DIR, "namespace.yaml")
JOB_MANIFEST = os.path.join(MANIFESTS_DIR, "job.yaml")
JOB_ERROR_MANIFEST = os.path.join(MANIFESTS_DIR, "job-with-error.yaml")


def setup_cluster() -> None:
    create_kind_cluster(CLUSTER_NAME)
    build_image(PIPELINE_DIR, IMAGE_TAG)
    load_image(CLUSTER_NAME, IMAGE_TAG)
    apply_manifest(NAMESPACE_MANIFEST)


def run_success_test() -> bool:
    print("\n--- Success path ---")
    apply_manifest(JOB_MANIFEST)
    try:
        status = wait_for_job("tracer-test", "simple-etl")
        logs = get_pod_logs("tracer-test", "app=simple-etl")
        print(f"Job status: {status}")
        print(f"Logs:\n{logs}")

        if status != "complete":
            print("FAIL: job did not complete successfully")
            return False
        if '"status": "processed"' not in logs:
            print('FAIL: logs missing "status": "processed"')
            return False

        print("PASS: success path verified")
        return True
    finally:
        delete_manifest(JOB_MANIFEST)


def run_failure_test() -> bool:
    print("\n--- Failure path ---")
    apply_manifest(JOB_ERROR_MANIFEST)
    try:
        status = wait_for_job("tracer-test", "simple-etl-error")
        logs = get_pod_logs("tracer-test", "app=simple-etl-error")
        print(f"Job status: {status}")
        print(f"Logs:\n{logs}")

        if status != "failed":
            print("FAIL: job should have failed")
            return False
        if "Injected ETL failure" not in logs:
            print('FAIL: logs missing "Injected ETL failure"')
            return False

        print("PASS: failure path verified")
        return True
    finally:
        delete_manifest(JOB_ERROR_MANIFEST)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Kubernetes ETL test")
    parser.add_argument("--success", action="store_true", help="Run success path only")
    parser.add_argument("--fail", action="store_true", help="Run failure path only")
    parser.add_argument("--both", action="store_true", help="Run both paths (default)")
    parser.add_argument("--keep-cluster", action="store_true", help="Don't delete kind cluster after test")
    args = parser.parse_args()

    run_both = not args.success and not args.fail

    missing = check_prerequisites_basic()
    if missing:
        print(f"Missing prerequisites: {', '.join(missing)}")
        print("Install with: brew install " + " ".join(missing))
        return 1

    passed = True
    try:
        setup_cluster()

        if (args.success or run_both) and not run_success_test():
            passed = False

        if (args.fail or run_both) and not run_failure_test():
            passed = False
    finally:
        if not args.keep_cluster:
            delete_kind_cluster(CLUSTER_NAME)

    status = "PASSED" if passed else "FAILED"
    print(f"\n{'=' * 60}")
    print(f"TEST {status}")
    print(f"{'=' * 60}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
