"""
Datadog Demo Orchestrator (local Kubernetes) — bioinformatics variant pipeline.

Runs 5 real Kubernetes Jobs on docker-desktop (no EKS needed):
  - Job 0: sample-batch-a  → ingest  succeeds (2 valid records)
  - Job 1: sample-batch-b  → ingest  succeeds (3 valid records)
  - Job 2: sample-batch-c  → validate FAILS  (S003 missing chromosome + quality_score)
  - Job 3: sample-batch-d  → validate FAILS  (S007 missing ref_allele + alt_allele)
  - Job 4: sample-batch-e  → validate FAILS  (S009 missing gene + chromosome)

Failing jobs use backoffLimit=2 so k8s retries 3 times total → BackoffLimitExceeded
(shows as multiple pod restarts in kubectl describe).

Real container stdout/stderr is captured via kubectl logs and shipped to Datadog
Logs Intake with correct kube-style tags so the log monitor fires → Slack.

Prerequisites:
    Docker Desktop running with Kubernetes enabled
    DD_API_KEY + DD_APP_KEY in .env

Run with:
    make datadog-demo
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from tests.e2e.kubernetes.infrastructure_sdk.local import (  # noqa: E402
    create_or_update_monitor,
    load_monitor_definitions,
)
from tests.utils.conftest import get_test_config  # noqa: E402

BASE_DIR = Path(__file__).parent
PIPELINE_DIR = BASE_DIR / "pipeline_code"
MONITOR_DEFS = str(BASE_DIR / "k8s_manifests" / "datadog-monitors.yaml")

NAMESPACE = "tracer-dd"
CLUSTER = "tracer-dd-demo"
PIPELINE_NAME = "bioinformatics_variant_pipeline"
IMAGE_TAG = "tracer-dd-pipeline:latest"
KUBE_CONTEXT = "docker-desktop"

# ---------------------------------------------------------------------------
# 5 sample batches: 2 succeed, 3 fail with distinct real validation errors
# ---------------------------------------------------------------------------
# Each entry: (batch_id, stage, records, backoff_limit)
# stage="ingest"   → always writes data, always succeeds → backoff_limit=0
# stage="validate" → fails if required field missing   → backoff_limit=2 (3 attempts)
_BATCHES = [
    # Job 0 — succeeds: 2 fully valid records
    (
        "sample-batch-a",
        "ingest",
        [
            {
                "sample_id": "S001",
                "gene": "BRCA1",
                "chromosome": "17",
                "position": 43044295,
                "ref_allele": "A",
                "alt_allele": "G",
                "quality_score": 99.2,
            },
            {
                "sample_id": "S002",
                "gene": "TP53",
                "chromosome": "17",
                "position": 7674220,
                "ref_allele": "C",
                "alt_allele": "T",
                "quality_score": 87.5,
            },
        ],
        0,
    ),
    # Job 1 — succeeds: 3 valid records
    (
        "sample-batch-b",
        "ingest",
        [
            {
                "sample_id": "S005",
                "gene": "PTEN",
                "chromosome": "10",
                "position": 89692905,
                "ref_allele": "G",
                "alt_allele": "A",
                "quality_score": 92.1,
            },
            {
                "sample_id": "S006",
                "gene": "RB1",
                "chromosome": "13",
                "position": 48941756,
                "ref_allele": "C",
                "alt_allele": "T",
                "quality_score": 78.3,
            },
            {
                "sample_id": "S010",
                "gene": "APC",
                "chromosome": "5",
                "position": 112707498,
                "ref_allele": "T",
                "alt_allele": "C",
                "quality_score": 95.0,
            },
        ],
        0,
    ),
    # Job 2 — FAILS: S003 missing chromosome + quality_score → 3 attempts (backoff=2)
    (
        "sample-batch-c",
        "validate",
        [
            {
                "sample_id": "S001",
                "gene": "BRCA1",
                "chromosome": "17",
                "position": 43044295,
                "ref_allele": "A",
                "alt_allele": "G",
                "quality_score": 99.2,
            },
            {
                "sample_id": "S003",
                "gene": "EGFR",
                "position": 55174772,
                "ref_allele": "G",
                "alt_allele": "A",
            },  # missing: chromosome, quality_score
        ],
        2,
    ),
    # Job 3 — FAILS: S007 missing ref_allele + alt_allele → 3 attempts (backoff=2)
    (
        "sample-batch-d",
        "validate",
        [
            {
                "sample_id": "S005",
                "gene": "PTEN",
                "chromosome": "10",
                "position": 89692905,
                "ref_allele": "G",
                "alt_allele": "A",
                "quality_score": 92.1,
            },
            {
                "sample_id": "S007",
                "gene": "VHL",
                "chromosome": "3",
                "position": 10183671,
                "quality_score": 61.4,
            },  # missing: ref_allele, alt_allele
        ],
        2,
    ),
    # Job 4 — FAILS: S009 missing gene + chromosome → 3 attempts (backoff=2)
    (
        "sample-batch-e",
        "validate",
        [
            {
                "sample_id": "S006",
                "gene": "RB1",
                "chromosome": "13",
                "position": 48941756,
                "ref_allele": "C",
                "alt_allele": "T",
                "quality_score": 78.3,
            },
            {
                "sample_id": "S009",
                "position": 7577120,
                "ref_allele": "T",
                "alt_allele": "C",
                "quality_score": 55.0,
            },  # missing: gene, chromosome
        ],
        2,
    ),
]


# ---------------------------------------------------------------------------
# Datadog API helper
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


# ---------------------------------------------------------------------------
# kubectl helpers
# ---------------------------------------------------------------------------


def _kubectl(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = ["kubectl", "--context", KUBE_CONTEXT, *args]
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def _ensure_namespace() -> None:
    result = _kubectl("get", "namespace", NAMESPACE, check=False)
    if result.returncode != 0:
        _kubectl("create", "namespace", NAMESPACE)
        print(f"  Created namespace {NAMESPACE}")
    else:
        print(f"  Namespace {NAMESPACE} exists")


def _delete_old_jobs(run_id: str) -> None:
    """Delete any leftover jobs from previous runs to avoid name conflicts."""
    for batch_id, _, _, _ in _BATCHES:
        _kubectl("delete", "job", batch_id, "-n", NAMESPACE, "--ignore-not-found", check=False)


def _create_job(
    batch_id: str, stage: str, records: list[dict], backoff_limit: int, run_id: str
) -> None:
    """Create a k8s Job for one pipeline batch. Records are passed via env var JSON."""
    restart_policy = "Never"  # Always Never so failed pods stay alive for log collection

    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": batch_id,
            "namespace": NAMESPACE,
            "labels": {
                "pipeline": PIPELINE_NAME,
                "run_id": run_id,
                "stage": stage,
                "cluster": CLUSTER,
            },
        },
        "spec": {
            "backoffLimit": backoff_limit,
            "template": {
                "metadata": {
                    "labels": {
                        "pipeline": PIPELINE_NAME,
                        "run_id": run_id,
                        "stage": stage,
                        "batch_id": batch_id,
                    },
                },
                "spec": {
                    "restartPolicy": restart_policy,
                    "containers": [
                        {
                            "name": stage,
                            "image": IMAGE_TAG,
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {"name": "PIPELINE_STAGE", "value": stage},
                                {"name": "PIPELINE_NAME", "value": PIPELINE_NAME},
                                {"name": "PIPELINE_RUN_ID", "value": batch_id},
                                {"name": "RECORDS_JSON", "value": json.dumps(records)},
                            ],
                        }
                    ],
                },
            },
        },
    }

    proc = subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, "apply", "-f", "-", "-n", NAMESPACE],
        input=json.dumps(manifest),
        capture_output=True,
        text=True,
        check=True,
    )
    _ = proc  # applied


def _wait_for_jobs(timeout_seconds: int = 180) -> dict[str, dict]:
    """Wait until all jobs are complete (succeeded or failed). Returns status per job."""
    batch_ids = [b[0] for b in _BATCHES]
    deadline = time.time() + timeout_seconds
    statuses: dict[str, dict] = {}

    print(f"  Waiting up to {timeout_seconds}s for {len(batch_ids)} jobs...")
    while time.time() < deadline:
        result = _kubectl(
            "get",
            "jobs",
            "-n",
            NAMESPACE,
            "-o",
            "json",
            "--selector",
            f"pipeline={PIPELINE_NAME}",
            check=False,
        )
        if result.returncode != 0:
            time.sleep(3)
            continue

        items = json.loads(result.stdout).get("items", [])
        done = 0
        statuses = {}
        for item in items:
            name = item["metadata"]["name"]
            conds = item.get("status", {}).get("conditions", [])
            succeeded = item["status"].get("succeeded", 0)
            failed = item["status"].get("failed", 0)
            active = item["status"].get("active", 0)

            complete = any(c["type"] == "Complete" and c["status"] == "True" for c in conds)
            job_failed = any(c["type"] == "Failed" and c["status"] == "True" for c in conds)

            statuses[name] = {
                "succeeded": complete,
                "failed": job_failed,
                "active": active,
                "success_count": succeeded,
                "fail_count": failed,
            }
            if complete or job_failed:
                done += 1

        if done >= len(batch_ids):
            break

        pending = [n for n, s in statuses.items() if not s["succeeded"] and not s["failed"]]
        print(f"  Still running: {pending} ...")
        time.sleep(5)

    return statuses


def _get_pod_logs(batch_id: str, stage: str) -> tuple[str, str, list[str]]:
    """Get stdout+stderr from all pods of a job. Returns (stdout, stderr, pod_names)."""
    pods_result = _kubectl(
        "get",
        "pods",
        "-n",
        NAMESPACE,
        "-l",
        f"batch_id={batch_id}",
        "-o",
        "jsonpath={.items[*].metadata.name}",
        check=False,
    )
    pod_names = pods_result.stdout.strip().split() if pods_result.stdout.strip() else []

    all_stdout: list[str] = []
    all_stderr: list[str] = []

    for pod_name in pod_names:
        logs_result = _kubectl(
            "logs",
            pod_name,
            "-n",
            NAMESPACE,
            "--all-containers",
            "--previous=false",
            check=False,
        )
        if logs_result.stdout:
            all_stdout.extend(logs_result.stdout.splitlines())
        if logs_result.stderr:
            all_stderr.extend(logs_result.stderr.splitlines())

        prev_result = _kubectl(
            "logs",
            pod_name,
            "-n",
            NAMESPACE,
            "--previous",
            check=False,
        )
        if prev_result.stdout:
            all_stdout.extend(f"[prev] {line}" for line in prev_result.stdout.splitlines())

    return "\n".join(all_stdout), "\n".join(all_stderr), pod_names


# ---------------------------------------------------------------------------
# Ship real container logs to Datadog
# ---------------------------------------------------------------------------


def _ship_job_logs(
    batch_id: str,
    stage: str,
    stdout: str,
    stderr: str,
    pod_names: list[str],
    job_status: dict,
    run_id: str,
) -> None:
    """Send real kubectl logs from k8s pods to Datadog Logs Intake."""
    succeeded = job_status.get("succeeded", False)
    fail_count = job_status.get("fail_count", 0)

    entries: list[dict] = []

    def _base(pod_name: str) -> dict:
        return {
            "ddsource": "kubernetes",
            "ddtags": (
                f"kube_namespace:{NAMESPACE},"
                f"pod_name:{pod_name},"
                f"container_name:{stage},"
                f"kube_job:{batch_id},"
                f"cluster:{CLUSTER},"
                f"pipeline:{PIPELINE_NAME},"
                f"run_id:{run_id},"
                f"stage:{stage}"
            ),
            "hostname": f"{CLUSTER}-control-plane",
            "service": "variant-pipeline",
            # Top-level JSON attributes — required for {{@field}} template vars in monitor messages
            "pod_name": pod_name,
            "container_name": stage,
            "kube_job": batch_id,
            "kube_namespace": NAMESPACE,
            "cluster": CLUSTER,
            "pipeline": PIPELINE_NAME,
            "run_id": run_id,
        }

    primary_pod = pod_names[0] if pod_names else f"{batch_id}-pod"

    for line in stdout.splitlines():
        entries.append({**_base(primary_pod), "message": line, "status": "info"})

    for line in stderr.splitlines():
        entries.append({**_base(primary_pod), "message": line, "status": "error"})

    for pod_name in pod_names or [primary_pod]:
        status_str = "succeeded" if succeeded else f"failed (attempts={fail_count})"
        entries.append(
            {
                **_base(pod_name),
                "message": (
                    f"[pod-lifecycle] pod={pod_name} job={batch_id} stage={stage} "
                    f"status={status_str} run_id={run_id}"
                ),
                "status": "info" if succeeded else "error",
            }
        )

    if entries:
        _dd("POST", "/api/v2/logs", entries, intake=True)


# ---------------------------------------------------------------------------
# Build Docker image + load into k8s
# ---------------------------------------------------------------------------


def _build_image() -> None:
    subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, str(PIPELINE_DIR)],
        check=True,
        capture_output=True,
    )


def _load_image_into_k8s() -> None:
    """Import the Docker image into Docker Desktop k8s containerd via docker save | ctr import."""
    nodes_result = _kubectl(
        "get",
        "nodes",
        "-o",
        "jsonpath={.items[*].metadata.name}",
        check=False,
    )
    nodes = nodes_result.stdout.strip().split() if nodes_result.stdout.strip() else []
    if not nodes:
        print("  No nodes found — skipping image load")
        return

    save_proc = subprocess.run(
        ["docker", "save", IMAGE_TAG],
        capture_output=True,
        check=True,
    )
    image_tar = save_proc.stdout

    for node in nodes:
        # Use kubectl debug (ephemeral container) to stream the tar into ctr import
        result = subprocess.run(
            [
                "docker",
                "exec",
                node,  # works when cluster is kind; for Docker Desktop use host containerd
                "ctr",
                "--namespace",
                "k8s.io",
                "images",
                "import",
                "-",
            ],
            input=image_tar,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"  Loaded image into node {node} via ctr")
        else:
            # Docker Desktop k8s: nodes are VM-internal — fall back to kind load if available
            kind_result = subprocess.run(
                ["kind", "load", "docker-image", IMAGE_TAG],
                capture_output=True,
                check=False,
            )
            if kind_result.returncode == 0:
                print("  Loaded image via kind load")
            else:
                print(
                    f"  Warning: could not load image into node {node} — imagePullPolicy=IfNotPresent will use cached copy"
                )
            break


# ---------------------------------------------------------------------------
# Patch pipeline code to accept RECORDS_JSON env var
# ---------------------------------------------------------------------------


def _patch_pipeline_for_k8s() -> None:
    """Ensure stages read RECORDS_JSON env var so the orchestrator controls records."""
    ingest_path = PIPELINE_DIR / "stages" / "ingest.py"
    current = ingest_path.read_text()

    if "RECORDS_JSON" in current:
        return

    patched = '''"""Ingest stage: read variant records from RECORDS_JSON env var (k8s) or defaults."""

import json
import os
import sys

from config import PIPELINE_NAME, PIPELINE_RUN_ID

_STAGING_PATH = "/tmp/staging"

_DEFAULT_RECORDS = [
    {"sample_id": "S001", "gene": "BRCA1", "chromosome": "17", "position": 43044295,
     "ref_allele": "A", "alt_allele": "G", "quality_score": 99.2},
    {"sample_id": "S002", "gene": "TP53", "chromosome": "17", "position": 7674220,
     "ref_allele": "C", "alt_allele": "T", "quality_score": 87.5},
]


def main() -> None:
    os.makedirs(_STAGING_PATH, exist_ok=True)
    output = os.path.join(_STAGING_PATH, f"{PIPELINE_RUN_ID}_raw.json")

    records_env = os.environ.get("RECORDS_JSON")
    if records_env:
        try:
            records = json.loads(records_env)
        except json.JSONDecodeError as e:
            print(f"PIPELINE_ERROR: Invalid RECORDS_JSON: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        records = _DEFAULT_RECORDS

    with open(output, "w") as f:
        json.dump({"pipeline": PIPELINE_NAME, "run_id": PIPELINE_RUN_ID, "records": records}, f)

    print(json.dumps({
        "stage": "ingest",
        "status": "success",
        "pipeline": PIPELINE_NAME,
        "run_id": PIPELINE_RUN_ID,
        "record_count": len(records),
        "output": output,
    }))
'''
    ingest_path.write_text(patched)


def _patch_validate_for_k8s() -> None:
    """Ensure validate stage seeds staging dir from RECORDS_JSON when no raw file exists."""
    validate_path = PIPELINE_DIR / "stages" / "validate.py"
    current = validate_path.read_text()

    if "RECORDS_JSON" in current:
        return

    patched = '''"""Validate stage: enforce schema on ingested variant records. Fails on bad data."""

import json
import os
import sys

from config import PIPELINE_NAME, PIPELINE_RUN_ID, REQUIRED_FIELDS
from errors import ValidationError

_STAGING_PATH = "/tmp/staging"


def _load_records() -> list[dict]:
    path = f"{_STAGING_PATH}/{PIPELINE_RUN_ID}_raw.json"
    if not os.path.exists(path):
        # In k8s, records are passed via RECORDS_JSON — seed staging dir
        records_env = os.environ.get("RECORDS_JSON")
        if not records_env:
            raise FileNotFoundError(f"No staging file at {path} and no RECORDS_JSON env var")
        os.makedirs(_STAGING_PATH, exist_ok=True)
        records = json.loads(records_env)
        with open(path, "w") as f:
            json.dump({"pipeline": PIPELINE_NAME, "run_id": PIPELINE_RUN_ID, "records": records}, f)
        return records
    with open(path) as f:
        return json.load(f)["records"]


def _validate(records: list[dict]) -> None:
    for i, record in enumerate(records):
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        if missing:
            raise ValidationError(
                f"PIPELINE_ERROR: Schema validation failed for record {i} "
                f"(sample_id={record.get(\'sample_id\', \'?\')}): missing fields {missing}"
            )


def main() -> None:
    records = _load_records()
    print(f"[validate] Checking {len(records)} records against schema {REQUIRED_FIELDS}")

    try:
        _validate(records)
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        print(json.dumps({
            "stage": "validate",
            "status": "failed",
            "pipeline": PIPELINE_NAME,
            "run_id": PIPELINE_RUN_ID,
            "error": str(e),
        }))
        sys.exit(1)

    print(json.dumps({
        "stage": "validate",
        "status": "success",
        "pipeline": PIPELINE_NAME,
        "run_id": PIPELINE_RUN_ID,
        "record_count": len(records),
    }))
'''
    validate_path.write_text(patched)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    get_test_config()

    if not os.environ.get("DD_API_KEY") or not os.environ.get("DD_APP_KEY"):
        print("DD_API_KEY and DD_APP_KEY must be set in .env")
        return 1

    run_id = f"dd-{uuid.uuid4().hex[:8]}"
    dd_site = os.environ.get("DD_SITE", "datadoghq.com")
    print(f"Run ID: {run_id}  [{datetime.now(UTC).strftime('%H:%M:%S')} UTC]")

    # 1. Upsert Datadog monitors
    print("\n[1/5] Upserting Datadog monitors...")
    defs = load_monitor_definitions(MONITOR_DEFS)
    monitor_ids: dict[str, int] = {}
    for d in defs:
        result = create_or_update_monitor(d)
        mid = result.get("id") or result.get("monitor", {}).get("id")
        monitor_ids[d["name"]] = mid
        print(f"  [{mid}] {d['name']}")

    # 2. Patch pipeline code + build Docker image + load into k8s
    print(f"\n[2/5] Patching pipeline code and building image {IMAGE_TAG}...")
    _patch_pipeline_for_k8s()
    _patch_validate_for_k8s()
    _build_image()
    _load_image_into_k8s()
    print("  Image ready")

    # 3. Create k8s namespace + Jobs
    print(f"\n[3/5] Creating {len(_BATCHES)} Kubernetes Jobs in namespace {NAMESPACE}...")
    _ensure_namespace()
    _delete_old_jobs(run_id)

    for batch_id, stage, records, backoff_limit in _BATCHES:
        _create_job(batch_id, stage, records, backoff_limit, run_id)
        retry_note = (
            f"backoff={backoff_limit} (up to {backoff_limit + 1} attempts)"
            if backoff_limit > 0
            else "no retry"
        )
        print(f"  Created job/{batch_id}  stage={stage}  {retry_note}")

    # 4. Wait for all jobs to complete or fail
    print("\n[4/5] Waiting for jobs to complete...")
    job_statuses = _wait_for_jobs(timeout_seconds=300)

    succeeded_jobs = [n for n, s in job_statuses.items() if s["succeeded"]]
    failed_jobs = [n for n, s in job_statuses.items() if s["failed"]]

    for name, status in job_statuses.items():
        icon = "✓" if status["succeeded"] else "✗"
        if status["succeeded"]:
            batch = next((b for b in _BATCHES if b[0] == name), None)
            stage_name = batch[1] if batch else "?"
            outcome = f"stage={stage_name}  exit=0"
        else:
            batch = next((b for b in _BATCHES if b[0] == name), None)
            stage_name = batch[1] if batch else "?"
            outcome = f"stage={stage_name}  exit=1  ({status['fail_count']} attempts)"
        print(f"  {icon} {name}  {outcome}")

    print(f"\n  {len(succeeded_jobs)} succeeded, {len(failed_jobs)} failed")

    # 5. Collect real pod logs + ship to Datadog
    print("\n[5/5] Collecting pod logs and shipping to Datadog...")

    for batch_id, stage, _, _ in _BATCHES:
        status = job_statuses.get(batch_id, {})
        stdout, stderr, pod_names = _get_pod_logs(batch_id, stage)
        _ship_job_logs(batch_id, stage, stdout, stderr, pod_names, status, run_id)
        line_count = len(stdout.splitlines()) + len(stderr.splitlines())
        print(f"  Shipped {batch_id} ({len(pod_names)} pods, {line_count} log lines)")

    # Post a summary event for all failures
    failed_details = "\n".join(
        f"  {b} (stage={s}): BackoffLimitExceeded after {job_statuses.get(b, {}).get('fail_count', 0)} attempts"
        for b, s, _, backoff in _BATCHES
        if backoff > 0
    )
    _dd(
        "POST",
        "/api/v1/events",
        {
            "title": f"[tracer-dd] {len(failed_jobs)}/5 jobs failed: {PIPELINE_NAME} ({run_id})",
            "text": (
                f"Run ID: {run_id}\n"
                f"Cluster: {CLUSTER}  Namespace: {NAMESPACE}\n"
                f"Context: {KUBE_CONTEXT}\n"
                f"Succeeded: {', '.join(succeeded_jobs) or 'none'}\n"
                f"Failed ({len(failed_jobs)}) — BackoffLimitExceeded:\n{failed_details}\n\n"
                f"Log query: PIPELINE_ERROR kube_namespace:{NAMESPACE}"
            ),
            "alert_type": "error",
            "priority": "normal",
            "tags": [
                f"cluster:{CLUSTER}",
                f"kube_namespace:{NAMESPACE}",
                f"pipeline:{PIPELINE_NAME}",
                f"run_id:{run_id}",
                f"failed_jobs:{len(failed_jobs)}",
                "source:tracer-agent",
                "env:local",
                "team:devops",
            ],
        },
    )

    print("\n" + "=" * 60)
    print(f"DONE — {len(failed_jobs)}/5 jobs failed, logs in Datadog")
    print("=" * 60)

    q_all = f"kube_namespace:{NAMESPACE} run_id:{run_id}"
    print(
        f"\nAll pods: https://app.{dd_site}/logs?query={q_all.replace(' ', '+').replace(':', '%3A')}"
    )

    print("\nFailed jobs:")
    for name in failed_jobs:
        q = f"kube_namespace:{NAMESPACE} kube_job:{name}"
        url = f"https://app.{dd_site}/logs?query={q.replace(' ', '+').replace(':', '%3A')}"
        print(f"  {name}  →  {url}")

    print("\nkubectl status:")
    print(f"  kubectl --context {KUBE_CONTEXT} get jobs -n {NAMESPACE}")
    print(f"  kubectl --context {KUBE_CONTEXT} get pods -n {NAMESPACE}")
    print(f"  kubectl --context {KUBE_CONTEXT} describe job <name> -n {NAMESPACE}")

    print("\nMonitors:")
    for _name, mid in monitor_ids.items():
        print(f"  [{mid}] https://app.{dd_site}/monitors/{mid}")

    print("\nSlack #devs-alerts notified by Datadog within ~5 min")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
