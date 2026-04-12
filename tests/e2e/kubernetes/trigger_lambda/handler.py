"""
Lambda handler for K8s ETL pipeline trigger.

Endpoints:
  POST /trigger              - Run pipeline with valid data (happy path)
  POST /trigger?inject_error=true - Run pipeline with bad data (schema error)

Flow:
  1. Upload test data to S3 landing bucket
  2. Submit etl-extract K8s job, wait for completion
  3. Submit etl-transform (or etl-transform-error) K8s job
  4. Return pipeline_run_id immediately (Datadog/Slack alert follows async)
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import ssl
import tempfile
import time
import urllib.request
import uuid
from datetime import UTC, datetime

import boto3
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

# Environment variables (set at deploy time)
CLUSTER_NAME = os.environ["CLUSTER_NAME"]
CLUSTER_ENDPOINT = os.environ["CLUSTER_ENDPOINT"]
CLUSTER_CA_DATA = os.environ["CLUSTER_CA_DATA"]
LANDING_BUCKET = os.environ["LANDING_BUCKET"]
PROCESSED_BUCKET = os.environ["PROCESSED_BUCKET"]
NAMESPACE = os.environ.get("NAMESPACE", "tracer-test")
REGION = os.environ.get("AWS_REGION", "us-east-1")
IMAGE_URI = os.environ["IMAGE_URI"]
SERVICE_ACCOUNT = os.environ.get("SERVICE_ACCOUNT", "etl-pipeline-sa")

VALID_PAYLOAD = {
    "data": [
        {
            "customer_id": "CUST-001",
            "order_id": "ORD-001",
            "amount": 99.99,
            "timestamp": "2026-01-01",
        },
        {
            "customer_id": "CUST-002",
            "order_id": "ORD-002",
            "amount": 149.50,
            "timestamp": "2026-01-01",
        },
    ]
}

INVALID_PAYLOAD = {
    "data": [
        {"order_id": "ORD-001", "amount": 99.99, "timestamp": "2026-01-01"},
    ]
}


# ---------------------------------------------------------------------------
# EKS auth + K8s API
# ---------------------------------------------------------------------------


def _get_eks_token() -> str:
    """Generate an EKS bearer token using botocore presigned STS request."""
    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()

    request = AWSRequest(
        method="GET",
        url="https://sts.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        headers={"x-k8s-aws-id": CLUSTER_NAME},
    )
    SigV4QueryAuth(credentials, "sts", REGION, expires=60).add_auth(request)

    return "k8s-aws-v1." + base64.urlsafe_b64encode(request.url.encode()).rstrip(b"=").decode()


def _k8s_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated request to the K8s API."""
    token = _get_eks_token()
    ca_bytes = base64.b64decode(CLUSTER_CA_DATA)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as f:
        f.write(ca_bytes)
        ca_file = f.name

    try:
        ctx = ssl.create_default_context(cafile=ca_file)
        url = f"{CLUSTER_ENDPOINT}{path}"
        data = json.dumps(body).encode() if body else None
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"K8s API error {e.code}: {e.read().decode()}") from e
    finally:
        os.unlink(ca_file)


def _delete_job(job_name: str) -> None:
    """Delete a K8s job if it exists."""
    with contextlib.suppress(RuntimeError):
        _k8s_request(
            "DELETE",
            f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/{job_name}",
            body={"propagationPolicy": "Background"},
        )


def _ensure_namespace_and_service_account() -> None:
    """Ensure namespace and service account exist for job submissions."""
    ns_path = f"/api/v1/namespaces/{NAMESPACE}"
    sa_path = f"/api/v1/namespaces/{NAMESPACE}/serviceaccounts/{SERVICE_ACCOUNT}"

    try:
        _k8s_request("GET", ns_path)
    except RuntimeError as err:
        if '"code":404' in str(err):
            _k8s_request(
                "POST",
                "/api/v1/namespaces",
                {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}},
            )
        else:
            raise

    try:
        _k8s_request("GET", sa_path)
    except RuntimeError as err:
        if '"code":404' in str(err):
            _k8s_request(
                "POST",
                f"/api/v1/namespaces/{NAMESPACE}/serviceaccounts",
                {
                    "apiVersion": "v1",
                    "kind": "ServiceAccount",
                    "metadata": {"name": SERVICE_ACCOUNT},
                },
            )
        else:
            raise


def _create_job(job_name: str, stage: str, env_vars: dict[str, str]) -> None:
    """Submit a K8s Job for the given pipeline stage."""
    env = [{"name": k, "value": v} for k, v in env_vars.items()]
    env.append({"name": "PIPELINE_STAGE", "value": stage})
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    env.extend(
        [
            {"name": "AWS_ACCESS_KEY_ID", "value": creds.access_key},
            {"name": "AWS_SECRET_ACCESS_KEY", "value": creds.secret_key},
            {"name": "AWS_SESSION_TOKEN", "value": creds.token or ""},
            {"name": "AWS_REGION", "value": REGION},
            {"name": "AWS_DEFAULT_REGION", "value": REGION},
        ]
    )

    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": NAMESPACE,
            "labels": {"app": "etl-pipeline", "stage": stage, "tracer": "test-case"},
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": {"app": "etl-pipeline", "stage": stage}},
                "spec": {
                    "serviceAccountName": SERVICE_ACCOUNT,
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": stage,
                            "image": IMAGE_URI,
                            "imagePullPolicy": "Always",
                            "env": env,
                        }
                    ],
                },
            },
        },
    }
    _k8s_request("POST", f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs", manifest)


def _wait_for_job(job_name: str, timeout: int = 90) -> str:
    """Poll until job completes or fails. Returns 'complete', 'failed', or 'timeout'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = _k8s_request("GET", f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/{job_name}")
        conditions = resp.get("status", {}).get("conditions", [])
        for cond in conditions:
            if cond.get("type") == "Complete" and cond.get("status") == "True":
                return "complete"
            if cond.get("type") == "Failed" and cond.get("status") == "True":
                return "failed"
        time.sleep(2)
    return "timeout"


# ---------------------------------------------------------------------------
# S3 data upload
# ---------------------------------------------------------------------------


def _upload_data(payload: dict) -> tuple[str, str]:
    """Upload test data to S3. Returns (s3_key, correlation_id)."""
    s3 = boto3.client("s3")
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    key = f"ingested/{ts}/data.json"
    correlation_id = f"lambda-{ts}"

    s3.put_object(
        Bucket=LANDING_BUCKET,
        Key=key,
        Body=json.dumps(payload),
        ContentType="application/json",
        Metadata={"correlation_id": correlation_id},
    )
    return key, correlation_id


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context) -> dict:
    inject_error = (event.get("queryStringParameters", {}) or {}).get(
        "inject_error", ""
    ).lower() == "true"

    run_id = f"api-{uuid.uuid4().hex[:8]}"
    payload = INVALID_PAYLOAD if inject_error else VALID_PAYLOAD

    try:
        # Upload data to S3
        s3_key, correlation_id = _upload_data(payload)

        # Ensure base namespace primitives exist
        _ensure_namespace_and_service_account()

        # Clean up stale jobs
        for job in ("etl-extract", "etl-transform", "etl-transform-error"):
            _delete_job(job)
        time.sleep(2)

        # Common env vars for all stages
        base_env = {
            "LANDING_BUCKET": LANDING_BUCKET,
            "PROCESSED_BUCKET": PROCESSED_BUCKET,
            "PIPELINE_RUN_ID": run_id,
        }

        # Submit extract job, wait for it to complete
        _create_job("etl-extract", "extract", {**base_env, "S3_KEY": s3_key})
        extract_status = _wait_for_job("etl-extract", timeout=90)
        if extract_status != "complete":
            return _response(
                500,
                {
                    "error": f"Extract job {extract_status}",
                    "pipeline_run_id": run_id,
                },
            )

        # Submit transform job (will fail on bad data -- async, let Datadog catch it)
        transform_job = "etl-transform-error" if inject_error else "etl-transform"
        _create_job(transform_job, "transform", base_env)

        return _response(
            200,
            {
                "status": "submitted",
                "pipeline_run_id": run_id,
                "inject_error": inject_error,
                "s3_key": s3_key,
                "correlation_id": correlation_id,
                "transform_job": transform_job,
            },
        )

    except Exception as e:
        return _response(500, {"error": str(e), "pipeline_run_id": run_id})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
