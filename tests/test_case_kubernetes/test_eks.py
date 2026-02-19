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
    python -m tests.test_case_kubernetes.test_eks
    python -m tests.test_case_kubernetes.test_eks --skip-deploy --skip-destroy
    python -m tests.test_case_kubernetes.test_eks --skip-verify
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

from tests.test_case_kubernetes.infrastructure_sdk.eks import (
    deploy_eks_stack,
    destroy_eks_stack,
    get_ecr_image_uri,
    update_kubeconfig,
)
from tests.test_case_kubernetes.infrastructure_sdk.local import (
    apply_manifest,
    delete_manifest,
    deploy_datadog_helm,
    get_pod_logs,
    wait_for_datadog_agent,
    wait_for_job,
)
from tests.test_case_kubernetes.test_datadog import (
    cleanup_monitors,
    deploy_monitors,
    verify_logs_in_datadog,
    verify_monitor_triggered,
)

NAMESPACE = "tracer-test"

BASE_DIR = os.path.dirname(__file__)
MANIFESTS_DIR = os.path.join(BASE_DIR, "k8s_manifests")

NAMESPACE_MANIFEST = os.path.join(MANIFESTS_DIR, "namespace.yaml")
DATADOG_VALUES_EKS = os.path.join(MANIFESTS_DIR, "datadog-values-eks.yaml")
JOB_ERROR_MANIFEST = os.path.join(MANIFESTS_DIR, "job-with-error.yaml")


def check_eks_prerequisites() -> list[str]:
    missing = []
    for tool in ("kubectl", "helm", "docker", "aws"):
        if shutil.which(tool) is None:
            missing.append(tool)
    return missing


def apply_manifest_with_image(manifest_path: str, image_uri: str) -> str:
    """Read a manifest, replace the image and imagePullPolicy, write a temp copy, apply it."""
    with open(manifest_path) as f:
        content = f.read()

    content = content.replace("image: tracer-k8s-test:latest", f"image: {image_uri}")
    content = content.replace("imagePullPolicy: Never", "imagePullPolicy: Always")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    apply_manifest(tmp_path)
    return tmp_path


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
    tmp_manifest = None
    try:
        if not args.skip_deploy:
            deploy_eks_stack()
        else:
            update_kubeconfig()

        image_uri = get_ecr_image_uri()
        print(f"Using ECR image: {image_uri}")

        apply_manifest(NAMESPACE_MANIFEST)
        deploy_datadog_helm(DATADOG_VALUES_EKS, NAMESPACE)

        if not wait_for_datadog_agent(NAMESPACE, timeout=300):
            print("FAIL: Datadog Agent did not become ready")
            return 1

        monitors_deployed = []
        if not args.skip_monitors and os.environ.get("DD_APP_KEY"):
            monitors_deployed = deploy_monitors()

        print("\n--- Running error job on EKS ---")
        tmp_manifest = apply_manifest_with_image(JOB_ERROR_MANIFEST, image_uri)
        status = wait_for_job(NAMESPACE, "simple-etl-error", timeout=180)
        logs = get_pod_logs(NAMESPACE, "app=simple-etl-error")
        print(f"Job status: {status}")
        print(f"Pod logs:\n{logs}")

        if status != "failed":
            print("FAIL: job should have failed")
            passed = False

        if "Injected ETL failure" not in logs:
            print("FAIL: expected error not in pod logs")
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

        delete_manifest(tmp_manifest or JOB_ERROR_MANIFEST)
    finally:
        if tmp_manifest:
            os.unlink(tmp_manifest)
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
