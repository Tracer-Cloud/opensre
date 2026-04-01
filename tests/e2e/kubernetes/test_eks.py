#!/usr/bin/env python3
"""
Kubernetes + Datadog integration test on AWS EKS.

Deploys an EKS cluster, pushes the ETL job image to ECR,
installs the Datadog Agent via Helm, runs a failing job,
and verifies logs arrive in Datadog.

Prerequisites:
    brew install kubectl helm awscli
    Docker Desktop running
    AWS credentials configured (AWS_ACCESS_KEY_ID, etc.)
    DD_API_KEY environment variable set
    DD_APP_KEY environment variable set (for log query verification)

Usage (from project root):
    python -m tests.e2e.kubernetes.test_eks
    python -m tests.e2e.kubernetes.test_eks --skip-deploy --skip-destroy
    python -m tests.e2e.kubernetes.test_eks --skip-verify
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid

from tests.e2e.kubernetes.infrastructure_sdk.eks import (
    deploy_eks_stack,
    destroy_eks_stack,
    ensure_nodegroup_capacity,
    update_kubeconfig,
)
from tests.e2e.kubernetes.infrastructure_sdk.local import (
    apply_manifest,
    deploy_datadog_helm,
    get_pod_logs,
    wait_for_datadog_agent,
    wait_for_job,
)
from tests.e2e.kubernetes.test_datadog import (
    cleanup_monitors,
    deploy_monitors,
    verify_logs_in_datadog,
    verify_monitor_triggered,
)
from tests.e2e.kubernetes.trigger_alert import (
    _apply_manifest,
    _delete_job,
    _render_eks_manifest,
)
from tests.shared.infrastructure_sdk.config import load_outputs
from tests.shared.infrastructure_sdk.trigger_config import discover_runtime_outputs
from tests.utils.s3_upload_validate import INVALID_PAYLOAD, upload_test_data

NAMESPACE = "tracer-test"

BASE_DIR = os.path.dirname(__file__)
MANIFESTS_DIR = os.path.join(BASE_DIR, "k8s_manifests")

NAMESPACE_MANIFEST = os.path.join(MANIFESTS_DIR, "namespace.yaml")
DATADOG_VALUES_EKS = os.path.join(MANIFESTS_DIR, "datadog-values-eks.yaml")
JOB_EXTRACT_MANIFEST = os.path.join(MANIFESTS_DIR, "job-extract.yaml")
JOB_TRANSFORM_ERROR_MANIFEST = os.path.join(MANIFESTS_DIR, "job-transform-error.yaml")


def check_eks_prerequisites() -> list[str]:
    missing = []
    for tool in ("kubectl", "helm", "docker", "aws"):
        if shutil.which(tool) is None:
            missing.append(tool)
    return missing




def main() -> int:
    parser = argparse.ArgumentParser(description="EKS + Datadog integration test")
    parser.add_argument("--skip-deploy", action="store_true", help="Skip EKS stack deployment (reuse existing)")
    parser.add_argument("--skip-destroy", action="store_true", help="Don't tear down EKS stack after test")
    parser.add_argument("--skip-verify", action="store_true", help="Skip Datadog API log verification")
    parser.add_argument("--skip-monitors", action="store_true", help="Skip monitor deployment and verification")
    parser.add_argument("--cleanup-monitors", action="store_true", help="Delete test monitors on exit")
    args = parser.parse_args()

    missing = check_eks_prerequisites()
    if missing:
        print(f"Missing prerequisites: {', '.join(missing)}")
        return 1

    if not os.environ.get("DD_API_KEY"):
        print("DD_API_KEY environment variable is required")
        return 1

    passed = True
    try:
        if not args.skip_deploy:
            deploy_eks_stack()
        else:
            update_kubeconfig()
            ensure_nodegroup_capacity()

        try:
            config = load_outputs("tracer-eks-k8s-test")
        except FileNotFoundError:
            fallback = discover_runtime_outputs()
            if not fallback:
                print("FAIL: Could not resolve EKS runtime outputs from local file or AWS tags")
                return 1
            config = fallback
        image_uri = config["ecr_image_uri"]
        print(f"Using ECR image: {image_uri}")

        apply_manifest(NAMESPACE_MANIFEST)
        deploy_datadog_helm(DATADOG_VALUES_EKS, NAMESPACE)

        if not wait_for_datadog_agent(NAMESPACE, timeout=300):
            print("FAIL: Datadog Agent did not become ready")
            return 1

        monitors_deployed = []
        if not args.skip_monitors and os.environ.get("DD_APP_KEY"):
            monitors_deployed = deploy_monitors()

        run_id = f"eks-test-{uuid.uuid4().hex[:8]}"
        test_data = upload_test_data(config["landing_bucket"], INVALID_PAYLOAD)

        common = {
            "landing_bucket": config["landing_bucket"],
            "processed_bucket": config["processed_bucket"],
            "s3_key": test_data.key,
            "pipeline_run_id": run_id,
            "image_uri": image_uri,
        }

        print("\n--- Running 3-stage pipeline on EKS (extract -> transform-error) ---")

        _delete_job("etl-extract")
        content = _render_eks_manifest(JOB_EXTRACT_MANIFEST, **common)
        _apply_manifest(content)
        status = wait_for_job(NAMESPACE, "etl-extract", timeout=180)
        if status != "complete":
            logs = get_pod_logs(NAMESPACE, "stage=extract")
            print(f"FAIL: extract did not complete ({status})\n{logs}")
            passed = False

        if passed:
            _delete_job("etl-transform-error")
            content = _render_eks_manifest(JOB_TRANSFORM_ERROR_MANIFEST, **common)
            _apply_manifest(content)
            status = wait_for_job(NAMESPACE, "etl-transform-error", timeout=180)
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
            import time
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
        if not args.skip_destroy and not args.skip_deploy:
            destroy_eks_stack()

    status_text = "PASSED" if passed else "FAILED"
    print(f"\n{'=' * 60}")
    print(f"TEST {status_text}")
    print(f"{'=' * 60}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
