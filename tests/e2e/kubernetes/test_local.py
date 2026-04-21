#!/usr/bin/env python3
"""
Local Kubernetes test for multi-stage ETL pipeline on kind.

Runs 3 K8s Jobs (extract -> transform -> load) against real S3 buckets.

Prerequisites:
    brew install kind kubectl
    Docker Desktop running
    AWS credentials configured (for S3 access from jobs)

Usage (from project root):
    python -m tests.e2e.kubernetes.test_local
    python -m tests.e2e.kubernetes.test_local --success
    python -m tests.e2e.kubernetes.test_local --fail
    python -m tests.e2e.kubernetes.test_local --keep-cluster
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import uuid

from tests.e2e.kubernetes.infrastructure_sdk.local import (
    apply_manifest,
    build_image,
    check_prerequisites_basic,
    create_kind_cluster,
    delete_kind_cluster,
    get_pod_logs,
    load_image,
    wait_for_job,
)
from tests.shared.infrastructure_sdk.config import load_outputs
from tests.shared.infrastructure_sdk.trigger_config import discover_runtime_outputs
from tests.utils.s3_upload_validate import (
    INVALID_PAYLOAD,
    VALID_PAYLOAD,
    upload_test_data,
)

CLUSTER_NAME = "tracer-k8s-test"
IMAGE_TAG = "tracer-k8s-test:latest"
NAMESPACE = "tracer-test"

BASE_DIR = os.path.dirname(__file__)
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline_code")
MANIFESTS_DIR = os.path.join(BASE_DIR, "k8s_manifests")
NAMESPACE_MANIFEST = os.path.join(MANIFESTS_DIR, "namespace.yaml")

STAGES = {
    "extract": os.path.join(MANIFESTS_DIR, "job-extract.yaml"),
    "transform": os.path.join(MANIFESTS_DIR, "job-transform.yaml"),
    "load": os.path.join(MANIFESTS_DIR, "job-load.yaml"),
    "transform-error": os.path.join(MANIFESTS_DIR, "job-transform-error.yaml"),
}

JOB_NAMES = {
    "extract": "etl-extract",
    "transform": "etl-transform",
    "load": "etl-load",
    "transform-error": "etl-transform-error",
}


def _load_config() -> dict:
    try:
        outputs = load_outputs("tracer-eks-k8s-test")
    except FileNotFoundError:
        discovered = discover_runtime_outputs()
        if not discovered:
            raise
        outputs = discovered
    return {
        "landing_bucket": outputs["landing_bucket"],
        "processed_bucket": outputs["processed_bucket"],
    }


def _resolve_aws_creds() -> dict[str, str]:
    """Resolve AWS credentials from boto3 session (works with profiles, env vars, etc.)."""
    import boto3

    session = boto3.Session()
    creds = session.get_credentials()
    if not creds:
        return {}

    frozen = creds.get_frozen_credentials()
    result = {
        "AWS_ACCESS_KEY_ID": frozen.access_key,
        "AWS_SECRET_ACCESS_KEY": frozen.secret_key,
        "AWS_DEFAULT_REGION": session.region_name or "us-east-1",
    }
    if frozen.token:
        result["AWS_SESSION_TOKEN"] = frozen.token
    return result


def _should_skip_in_ci() -> bool:
    """Skip the infra-backed CI run when AWS credentials are unavailable."""
    return bool(os.getenv("CI")) and not _resolve_aws_creds()


def _render_manifest(
    manifest_path: str,
    *,
    landing_bucket: str,
    processed_bucket: str,
    s3_key: str,
    pipeline_run_id: str,
    image: str = "tracer-k8s-test:latest",
    image_pull_policy: str = "Never",
) -> str:
    """Render a manifest template, replacing {{KEY}} markers and injecting AWS creds."""
    with open(manifest_path) as f:
        content = f.read()

    content = (
        content.replace("{{LANDING_BUCKET}}", landing_bucket)
        .replace("{{PROCESSED_BUCKET}}", processed_bucket)
        .replace("{{S3_KEY}}", s3_key)
        .replace("{{PIPELINE_RUN_ID}}", pipeline_run_id)
        .replace("tracer-k8s-test:latest", image)
        .replace("imagePullPolicy: Never", f"imagePullPolicy: {image_pull_policy}")
    )

    # Inject AWS credentials so kind cluster jobs can access S3
    aws_creds = _resolve_aws_creds()
    aws_env_block = ""
    for var, val in aws_creds.items():
        aws_env_block += f'            - name: {var}\n              value: "{val}"\n'

    if aws_env_block:
        content = content.replace(
            "          env:\n",
            f"          env:\n{aws_env_block}",
        )

    return content


def _apply_rendered(content: str) -> str:
    """Write rendered manifest to temp file, apply it, return the path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        path = f.name
    from tests.e2e.kubernetes.infrastructure_sdk.local import _run

    _run(["kubectl", "apply", "-f", path], capture=False)
    return path


def _delete_job(job_name: str) -> None:
    from tests.e2e.kubernetes.infrastructure_sdk.local import _run

    _run(["kubectl", "delete", "job", job_name, "-n", NAMESPACE, "--ignore-not-found"], check=False)


def _run_stage(
    stage: str,
    *,
    landing_bucket: str,
    processed_bucket: str,
    s3_key: str,
    pipeline_run_id: str,
    expect_fail: bool = False,
) -> bool:
    """Run a single pipeline stage as a K8s Job. Returns True if outcome matches expectation."""
    job_name = JOB_NAMES[stage]
    manifest_path = STAGES[stage]

    _delete_job(job_name)

    content = _render_manifest(
        manifest_path,
        landing_bucket=landing_bucket,
        processed_bucket=processed_bucket,
        s3_key=s3_key,
        pipeline_run_id=pipeline_run_id,
    )
    tmp_path = _apply_rendered(content)

    try:
        status = wait_for_job(NAMESPACE, job_name)
        logs = get_pod_logs(NAMESPACE, f"stage={stage}")
        print(f"  [{stage}] status={status}")
        print(f"  [{stage}] logs: {logs[:500]}")

        if expect_fail:
            return status == "failed"
        return status == "complete"
    finally:
        os.unlink(tmp_path)


def setup_cluster() -> None:
    create_kind_cluster(CLUSTER_NAME)
    build_image(PIPELINE_DIR, IMAGE_TAG)
    load_image(CLUSTER_NAME, IMAGE_TAG)
    apply_manifest(NAMESPACE_MANIFEST)
    # Create the service account used by job manifests (on EKS this is created with IRSA)
    from tests.e2e.kubernetes.infrastructure_sdk.local import _run

    _run(
        ["kubectl", "create", "serviceaccount", "etl-pipeline-sa", "-n", NAMESPACE],
        check=False,
    )


def run_success_test(config: dict) -> bool:
    print("\n--- Success path (3-stage pipeline) ---")
    run_id = f"success-{uuid.uuid4().hex[:8]}"

    test_data = upload_test_data(config["landing_bucket"], VALID_PAYLOAD)
    s3_key = test_data.key

    common = {
        "landing_bucket": config["landing_bucket"],
        "processed_bucket": config["processed_bucket"],
        "s3_key": s3_key,
        "pipeline_run_id": run_id,
    }

    for stage in ("extract", "transform", "load"):
        print(f"\n  Running {stage}...")
        if not _run_stage(stage, **common):
            print(f"FAIL: {stage} did not complete")
            return False

    print("\nPASS: 3-stage pipeline completed successfully")
    return True


def run_failure_test(config: dict) -> bool:
    print("\n--- Failure path (transform fails on bad schema) ---")
    run_id = f"fail-{uuid.uuid4().hex[:8]}"

    test_data = upload_test_data(config["landing_bucket"], INVALID_PAYLOAD)
    s3_key = test_data.key

    common = {
        "landing_bucket": config["landing_bucket"],
        "processed_bucket": config["processed_bucket"],
        "s3_key": s3_key,
        "pipeline_run_id": run_id,
    }

    print("\n  Running extract...")
    if not _run_stage("extract", **common):
        print("FAIL: extract did not complete")
        return False

    print("\n  Running transform-error (expect failure)...")
    if not _run_stage("transform-error", **common, expect_fail=True):
        print("FAIL: transform should have failed")
        return False

    logs = get_pod_logs(NAMESPACE, "stage=transform-error")
    if "Schema validation failed" not in logs and "Missing fields" not in logs:
        print(f"FAIL: expected schema validation error in logs, got: {logs}")
        return False

    print("\nPASS: transform correctly failed on missing customer_id")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Local K8s multi-stage ETL test")
    parser.add_argument("--success", action="store_true", help="Run success path only")
    parser.add_argument("--fail", action="store_true", help="Run failure path only")
    parser.add_argument("--both", action="store_true", help="Run both paths (default)")
    parser.add_argument(
        "--keep-cluster", action="store_true", help="Don't delete kind cluster after test"
    )
    args = parser.parse_args()

    run_both = not args.success and not args.fail

    if _should_skip_in_ci():
        print("Skipping Kubernetes infra test in CI: AWS credentials are not configured.")
        return 0

    missing = check_prerequisites_basic()
    if missing:
        print(f"Missing prerequisites: {', '.join(missing)}")
        print("Install with: brew install " + " ".join(missing))
        return 1

    config = _load_config()

    passed = True
    try:
        setup_cluster()

        if (args.success or run_both) and not run_success_test(config):
            passed = False

        if (args.fail or run_both) and not run_failure_test(config):
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
