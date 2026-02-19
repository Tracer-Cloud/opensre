#!/usr/bin/env python3
"""
Fast alert trigger: run the 3-stage ETL pipeline on EKS with bad data.

Flow:
  1. Upload bad data to S3 (missing customer_id)
  2. Submit extract job -> wait for completion
  3. Submit transform job -> wait for failure (schema validation error)
  4. Poll Datadog Logs API until PIPELINE_ERROR appears
  5. Optionally verify Slack alert

Usage:
    python -m tests.test_case_kubernetes.trigger_alert
    python -m tests.test_case_kubernetes.trigger_alert --verify
    python -m tests.test_case_kubernetes.trigger_alert --configure-kubectl --verify
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

from tests.shared.infrastructure_sdk.config import load_outputs
from tests.shared.slack_polling import get_channel_id, poll_for_message
from tests.test_case_kubernetes.infrastructure_sdk.eks import (
    get_ecr_image_uri,
    update_kubeconfig,
)
from tests.utils.s3_upload_validate import INVALID_PAYLOAD, upload_test_data

NAMESPACE = "tracer-test"
BASE_DIR = os.path.dirname(__file__)
MANIFESTS_DIR = os.path.join(BASE_DIR, "k8s_manifests")


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _load_config() -> dict:
    outputs = load_outputs("tracer-eks-k8s-test")
    return {
        "landing_bucket": outputs["landing_bucket"],
        "processed_bucket": outputs["processed_bucket"],
        "ecr_image_uri": outputs["ecr_image_uri"],
    }


def _render_eks_manifest(
    manifest_path: str,
    *,
    landing_bucket: str,
    processed_bucket: str,
    s3_key: str,
    pipeline_run_id: str,
    image_uri: str,
) -> str:
    """Render manifest for EKS: replace templates, set ECR image, Always pull."""
    with open(manifest_path) as f:
        content = f.read()

    return (
        content.replace("{{LANDING_BUCKET}}", landing_bucket)
        .replace("{{PROCESSED_BUCKET}}", processed_bucket)
        .replace("{{S3_KEY}}", s3_key)
        .replace("{{PIPELINE_RUN_ID}}", pipeline_run_id)
        .replace("tracer-k8s-test:latest", image_uri)
        .replace("imagePullPolicy: Never", "imagePullPolicy: Always")
    )


def _apply_manifest(content: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        path = f.name
    _run(["kubectl", "apply", "-f", path])
    os.unlink(path)


def _delete_job(job_name: str) -> None:
    _run(["kubectl", "delete", "job", job_name, "-n", NAMESPACE, "--ignore-not-found"], check=False)


def _wait_for_job(job_name: str, timeout: int = 120) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            ["kubectl", "get", "job", job_name, "-n", NAMESPACE,
             "-o", "jsonpath={.status.conditions[*].type}"],
            check=False,
        )
        conditions = result.stdout.strip()
        if "Failed" in conditions:
            return "failed"
        if "Complete" in conditions:
            return "complete"
        time.sleep(1)
    return "timeout"


def _get_logs(label: str) -> str:
    result = _run(
        ["kubectl", "logs", "-l", label, "-n", NAMESPACE, "--all-containers=true"],
        check=False,
    )
    return (result.stdout + result.stderr).strip()


# ---------------------------------------------------------------------------
# Datadog Logs API
# ---------------------------------------------------------------------------

def _poll_datadog_logs(max_wait: int = 90) -> bool:
    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    site = os.environ.get("DD_SITE", "datadoghq.com")
    if not api_key or not app_key:
        return False

    print("Polling Datadog Logs API...")
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            payload = json.dumps({
                "filter": {
                    "query": "kube_namespace:tracer-test PIPELINE_ERROR",
                    "from": "now-2m",
                    "to": "now",
                },
                "sort": "-timestamp",
                "page": {"limit": 1},
            }).encode()
            url = f"https://api.{site}/api/v2/logs/events/search"
            req = urllib.request.Request(url, data=payload, headers={
                "DD-API-KEY": api_key,
                "DD-APPLICATION-KEY": app_key,
                "Content-Type": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
            if body.get("data"):
                elapsed = max_wait - int(deadline - time.monotonic())
                print(f"  Log found in Datadog ({elapsed}s)")
                return True
        except Exception as e:
            print(f"  Poll error: {e}")

        remaining = int(deadline - time.monotonic())
        print(f"  Not in DD yet... ({remaining}s remaining)")
        time.sleep(5)

    return False


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

_SLACK_KEYWORDS = ["PIPELINE_ERROR", "Pipeline error", "tracer"]


def query_slack_alerts(
    max_wait: int = 300,
    channel_id: str | None = None,
    since_epoch: float | None = None,
) -> bool:
    return poll_for_message(
        _SLACK_KEYWORDS,
        channel_id=channel_id,
        max_wait=max_wait,
        since_epoch=since_epoch,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Fast K8s 3-stage pipeline alert trigger")
    parser.add_argument("--configure-kubectl", action="store_true", help="Run aws eks update-kubeconfig first")
    parser.add_argument("--verify", action="store_true", help="Verify logs in DD + wait for DD alert in Slack")
    args = parser.parse_args()

    start = time.monotonic()
    start_epoch = time.time()

    if args.configure_kubectl:
        update_kubeconfig()

    config = _load_config()
    image_uri = config["ecr_image_uri"]
    run_id = f"alert-{uuid.uuid4().hex[:8]}"

    common = {
        "landing_bucket": config["landing_bucket"],
        "processed_bucket": config["processed_bucket"],
        "pipeline_run_id": run_id,
        "image_uri": image_uri,
    }

    # Upload bad data (missing customer_id)
    print("Uploading bad test data to S3...")
    test_data = upload_test_data(config["landing_bucket"], INVALID_PAYLOAD)

    # Stage 1: Extract
    print("\nCleaning up old jobs...")
    for job in ("etl-extract", "etl-transform", "etl-transform-error"):
        _delete_job(job)

    print("Submitting extract job to EKS...")
    extract_content = _render_eks_manifest(
        os.path.join(MANIFESTS_DIR, "job-extract.yaml"),
        s3_key=test_data.key,
        **common,
    )
    _apply_manifest(extract_content)

    print("Waiting for extract to complete...")
    status = _wait_for_job("etl-extract")
    if status != "complete":
        logs = _get_logs("stage=extract")
        print(f"FAIL: extract {status}\n{logs}")
        return 1
    print("  Extract completed")

    # Stage 2: Transform (expect failure)
    print("Submitting transform job to EKS...")
    transform_content = _render_eks_manifest(
        os.path.join(MANIFESTS_DIR, "job-transform-error.yaml"),
        s3_key=test_data.key,
        **common,
    )
    _apply_manifest(transform_content)

    print("Waiting for transform to fail...")
    status = _wait_for_job("etl-transform-error")
    logs = _get_logs("stage=transform-error")

    trigger_elapsed = time.monotonic() - start
    print(f"\nTransform status: {status} ({trigger_elapsed:.1f}s)")
    print(f"Pod logs: {logs}")

    if status != "failed" or "Schema validation failed" not in logs:
        print("FAIL: transform did not fail as expected")
        return 1

    print(f"\nAlert triggered in {trigger_elapsed:.1f}s")

    if not args.verify:
        print("Done. DD monitor will fire in ~1-2 min -> Slack alert follows.")
        return 0

    dd_found = _poll_datadog_logs(max_wait=90)
    dd_elapsed = time.monotonic() - start

    if dd_found:
        print(f"\nLog confirmed in Datadog ({dd_elapsed:.1f}s)")
    else:
        print(f"\nWARNING: Log not found in Datadog within timeout ({dd_elapsed:.1f}s)")

    print("Waiting for Datadog monitor to fire and post to Slack...")
    channel_id = get_channel_id()
    slack_found = query_slack_alerts(max_wait=300, channel_id=channel_id, since_epoch=start_epoch)

    # Cleanup
    for job in ("etl-extract", "etl-transform-error"):
        _delete_job(job)

    total = time.monotonic() - start
    if dd_found and slack_found:
        print(f"\nEnd-to-end verified: extract -> transform(fail) -> Datadog -> Slack ({total:.1f}s)")
    elif dd_found:
        print(f"\nPartial: log in Datadog but Slack alert not confirmed ({total:.1f}s)")
    else:
        print(f"\nFailed: log not found in Datadog ({total:.1f}s)")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
