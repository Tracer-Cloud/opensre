#!/usr/bin/env python3
"""
Fast alert trigger: submit a failing K8s job on EKS and verify in ~40 seconds.

Flow:
  1. Submit failing job to EKS (~9s)
  2. Poll Datadog Logs API until PIPELINE_ERROR appears (~20-30s)
  3. Post alert confirmation to Slack #devs-alerts (instant)
  Total: ~40s

The Datadog monitor also fires in the background (~1-2 min) for ongoing alerting.

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

from tests.test_case_kubernetes.infrastructure_sdk.eks import (
    get_ecr_image_uri,
    update_kubeconfig,
)

NAMESPACE = "tracer-test"
JOB_NAME = "simple-etl-error"

BASE_DIR = os.path.dirname(__file__)
MANIFESTS_DIR = os.path.join(BASE_DIR, "k8s_manifests")
JOB_ERROR_MANIFEST = os.path.join(MANIFESTS_DIR, "job-with-error.yaml")


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# K8s job operations
# ---------------------------------------------------------------------------

def _delete_old_job() -> None:
    _run(["kubectl", "delete", "job", JOB_NAME, "-n", NAMESPACE, "--ignore-not-found"], check=False)


def _apply_error_job(image_uri: str) -> None:
    with open(JOB_ERROR_MANIFEST) as f:
        content = f.read()
    content = content.replace("image: tracer-k8s-test:latest", f"image: {image_uri}")
    content = content.replace("imagePullPolicy: Never", "imagePullPolicy: Always")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    _run(["kubectl", "apply", "-f", tmp_path])
    os.unlink(tmp_path)


def _wait_for_failure(timeout: int = 60) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            ["kubectl", "get", "job", JOB_NAME, "-n", NAMESPACE,
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


def _get_logs() -> str:
    result = _run(
        ["kubectl", "logs", "-l", f"app={JOB_NAME}", "-n", NAMESPACE, "--all-containers=true"],
        check=False,
    )
    return (result.stdout + result.stderr).strip()


# ---------------------------------------------------------------------------
# Datadog Logs API (fast path -- logs appear in ~20-30s)
# ---------------------------------------------------------------------------

def _poll_datadog_logs(max_wait: int = 90) -> bool:
    """Poll Datadog Logs API until PIPELINE_ERROR appears."""
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
# Slack: post alert + read messages
# ---------------------------------------------------------------------------

def _get_slack_channel_id() -> str | None:
    channel_id = os.environ.get("SLACK_DEVS_ALERTS_CHANNEL_ID", "")
    if channel_id:
        return channel_id
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return None
    url = "https://slack.com/api/conversations.list?types=public_channel,private_channel&limit=200"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    for ch in data.get("channels", []):
        if ch["name"] == "devs-alerts":
            return ch["id"]
    return None


def _get_recent_messages(channel_id: str, oldest: str = "0") -> list[dict]:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return []
    url = f"https://slack.com/api/conversations.history?channel={channel_id}&oldest={oldest}&limit=10"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data.get("messages", [])


def query_slack_alerts(max_wait: int = 300, channel_id: str | None = None) -> bool:
    """Poll Slack #devs-alerts for a Datadog monitor alert message."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("SLACK_BOT_TOKEN not set, skipping Slack verification")
        return False

    if not channel_id:
        channel_id = _get_slack_channel_id()
    if not channel_id:
        print("Could not find #devs-alerts channel. Set SLACK_DEVS_ALERTS_CHANNEL_ID.")
        return False

    print(f"Polling Slack #devs-alerts for DD monitor alert (up to {max_wait}s)...")
    trigger_time = time.time()
    deadline = time.monotonic() + max_wait

    while time.monotonic() < deadline:
        messages = _get_recent_messages(channel_id, oldest=str(trigger_time - 60))
        for msg in messages:
            text = msg.get("text", "") + json.dumps(msg.get("attachments", []))
            if "PIPELINE_ERROR" in text or "Pipeline error" in text or "tracer" in text.lower():
                print("  DD monitor alert found in Slack!")
                preview = msg.get("text", "")[:120]
                print(f"  Message: {preview}")
                return True

        remaining = int(deadline - time.monotonic())
        print(f"  No DD alert yet, retrying... ({remaining}s remaining)")
        time.sleep(10)

    print("DD monitor alert did not appear in Slack within timeout")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Fast K8s alert trigger (~40s end-to-end)")
    parser.add_argument("--configure-kubectl", action="store_true", help="Run aws eks update-kubeconfig first")
    parser.add_argument("--verify", action="store_true", help="Verify logs in DD + wait for DD alert in Slack")
    args = parser.parse_args()

    start = time.monotonic()

    if args.configure_kubectl:
        update_kubeconfig()

    image_uri = get_ecr_image_uri()

    print("Cleaning up old job...")
    _delete_old_job()

    print("Submitting failing job to EKS...")
    _apply_error_job(image_uri)

    print("Waiting for job to fail...")
    status = _wait_for_failure()
    logs = _get_logs()

    trigger_elapsed = time.monotonic() - start
    print(f"\nJob status: {status} ({trigger_elapsed:.1f}s)")
    print(f"Pod logs: {logs}")

    if status != "failed" or "Injected ETL failure" not in logs:
        print("FAIL: job did not fail as expected")
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
    channel_id = _get_slack_channel_id()
    slack_found = query_slack_alerts(max_wait=300, channel_id=channel_id)

    _delete_old_job()

    total = time.monotonic() - start
    if dd_found and slack_found:
        print(f"\nEnd-to-end verified: job -> Datadog -> Slack ({total:.1f}s)")
    elif dd_found:
        print(f"\nPartial: log in Datadog but Slack alert not confirmed ({total:.1f}s)")
    else:
        print(f"\nFailed: log not found in Datadog ({total:.1f}s)")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
