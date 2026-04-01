#!/usr/bin/env python3
"""
Fast alert trigger and verification for Kubernetes test case.

Default mode triggers the failure via trigger API URL from centralized JSON config.
Config file: tests/shared/infrastructure_sdk/outputs/tracer-k8s-trigger.json

Usage:
    python -m tests.e2e.kubernetes.trigger_alert
    python -m tests.e2e.kubernetes.trigger_alert --verify
    python -m tests.e2e.kubernetes.trigger_alert --regen-config
    python -m tests.e2e.kubernetes.trigger_alert --verify-only --since-epoch 1771466422
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import boto3

from tests.e2e.kubernetes.infrastructure_sdk.eks import cluster_exists
from tests.shared.infrastructure_sdk.trigger_config import (
    load_trigger_config,
    regenerate_trigger_config,
)
from tests.shared.slack_polling import get_channel_id, poll_for_message


def _trigger_via_api(trigger_api_url: str) -> dict:
    url = trigger_api_url.rstrip("/") + "/trigger?inject_error=true"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 504:
            # API Gateway timed out waiting, but Lambda may still be executing.
            return {
                "status": "accepted_timeout",
                "http_status": 504,
                "raw_body": body,
            }
        raise RuntimeError(f"HTTP {exc.code} from trigger API: {body}") from exc
    return payload if isinstance(payload, dict) else {}


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _render_eks_manifest(
    manifest_path: str,
    *,
    landing_bucket: str,
    processed_bucket: str,
    s3_key: str,
    pipeline_run_id: str,
    image_uri: str,
) -> str:
    """Compatibility helper used by test_eks flow."""
    with open(manifest_path) as f:
        content = f.read()

    rendered = (
        content.replace("{{LANDING_BUCKET}}", landing_bucket)
        .replace("{{PROCESSED_BUCKET}}", processed_bucket)
        .replace("{{S3_KEY}}", s3_key)
        .replace("{{PIPELINE_RUN_ID}}", pipeline_run_id)
        .replace("tracer-k8s-test:latest", image_uri)
        .replace("imagePullPolicy: Never", "imagePullPolicy: Always")
    )

    creds = boto3.Session().get_credentials()
    frozen = creds.get_frozen_credentials() if creds else None
    if frozen:
        region = os.environ.get("AWS_REGION", "us-east-1")
        credentials_env = (
            f'            - name: AWS_ACCESS_KEY_ID\n'
            f'              value: "{frozen.access_key}"\n'
            f'            - name: AWS_SECRET_ACCESS_KEY\n'
            f'              value: "{frozen.secret_key}"\n'
            f'            - name: AWS_SESSION_TOKEN\n'
            f'              value: "{frozen.token or ""}"\n'
            f'            - name: AWS_REGION\n'
            f'              value: "{region}"\n'
            f'            - name: AWS_DEFAULT_REGION\n'
            f'              value: "{region}"\n'
        )
        rendered = rendered.replace(
            f'            - name: PIPELINE_RUN_ID\n'
            f'              value: "{pipeline_run_id}"\n',
            f'            - name: PIPELINE_RUN_ID\n'
            f'              value: "{pipeline_run_id}"\n'
            + credentials_env,
        )

    return rendered


def _apply_manifest(content: str) -> None:
    """Compatibility helper used by test_eks flow."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        _run(["kubectl", "apply", "-f", path])
    finally:
        os.unlink(path)


def _delete_job(job_name: str) -> None:
    """Compatibility helper used by test_eks flow."""
    _run(["kubectl", "delete", "job", job_name, "-n", "tracer-test", "--ignore-not-found"], check=False)


def _load_or_regen_trigger_config() -> dict:
    try:
        return load_trigger_config()
    except Exception:
        regenerate_trigger_config()
        return load_trigger_config()


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

def verify(since_epoch: float, *, dd_max_wait: int = 120, slack_max_wait: int = 300) -> int:
    """Poll Datadog and Slack to confirm the pipeline failure was observed.

    Returns 0 on success, 1 if Datadog verification fails.
    """
    start = time.monotonic()

    dd_found = _poll_datadog_logs(max_wait=dd_max_wait)
    dd_elapsed = time.monotonic() - start

    if not dd_found:
        print(f"\nFAIL: PIPELINE_ERROR not found in Datadog within {dd_max_wait}s")
        return 1

    print(f"\nLog confirmed in Datadog ({dd_elapsed:.1f}s)")
    print("Waiting for Datadog monitor to fire and post to Slack...")

    channel_id = get_channel_id()
    slack_found = query_slack_alerts(
        max_wait=slack_max_wait,
        channel_id=channel_id,
        since_epoch=since_epoch,
    )

    total = time.monotonic() - start
    if dd_found and slack_found:
        print(f"\nEnd-to-end verified: pipeline failure -> Datadog -> Slack ({total:.1f}s)")
    else:
        print(f"\nPartial: log in Datadog but Slack alert not confirmed ({total:.1f}s)")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="K8s trigger alert and verifier")
    parser.add_argument("--regen-config", action="store_true", help="Regenerate centralized trigger config JSON")
    parser.add_argument("--verify", action="store_true", help="Verify logs in DD + wait for DD alert in Slack")
    parser.add_argument("--verify-only", action="store_true", help="Skip trigger, only run DD + Slack verification")
    parser.add_argument("--since-epoch", type=float, default=None, help="Unix timestamp to search Slack from (for --verify-only)")
    args = parser.parse_args()

    if args.regen_config:
        try:
            path = regenerate_trigger_config()
            print(f"Trigger config regenerated: {path}")
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 1

    since_epoch = args.since_epoch or time.time()

    if args.verify_only:
        return verify(since_epoch)

    start_epoch = time.time()
    try:
        cfg = _load_or_regen_trigger_config()
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("Run: make regen-trigger-config")
        return 1

    trigger_api_url = cfg["trigger_api_url"]
    if not cluster_exists():
        print("ERROR: EKS cluster 'tracer-eks-test' is not available.")
        print("Trigger API exists but cannot run pipeline jobs without the cluster.")
        return 1
    print(f"Triggering pipeline via API: {trigger_api_url}")
    try:
        response = _trigger_via_api(trigger_api_url)
    except Exception as exc:
        print(f"ERROR: API trigger failed: {exc}")
        return 1

    print(f"Trigger response: {json.dumps(response)}")

    if not args.verify:
        print("Done. DD monitor will fire in ~1-2 min -> Slack alert follows.")
        return 0

    return verify(start_epoch)


if __name__ == "__main__":
    sys.exit(main())
