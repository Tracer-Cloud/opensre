"""
Lambda handler for /trigger endpoint.

Endpoints:
- POST /trigger - Run pipeline with valid data (happy path)
- POST /trigger?inject_error=true - Run pipeline with schema error (failed path)
"""

import json
import os
from datetime import datetime

import boto3
import requests

LANDING_BUCKET = os.environ.get("LANDING_BUCKET", "")
AIRFLOW_API_URL = os.environ.get("AIRFLOW_API_URL", "http://localhost:8080/api/v2")
AIRFLOW_API_USERNAME = os.environ.get("AIRFLOW_API_USERNAME", "admin")
AIRFLOW_API_PASSWORD = os.environ.get("AIRFLOW_API_PASSWORD", "admin")
AIRFLOW_DAG_ID = os.environ.get("AIRFLOW_DAG_ID", "upstream_downstream_pipeline_airflow")
EXTERNAL_API_URL = os.environ.get("EXTERNAL_API_URL", "")

s3_client = boto3.client("s3")


def fetch_from_external_api(api_url: str, inject_error: bool = False) -> tuple[dict, dict]:
    audit_info = {"requests": []}

    if inject_error:
        try:
            config_response = requests.post(
                f"{api_url}/config",
                json={"inject_schema_change": True},
                timeout=10,
            )
            audit_info["requests"].append(
                {
                    "type": "POST",
                    "url": f"{api_url}/config",
                    "request_body": {"inject_schema_change": True},
                    "status_code": config_response.status_code,
                    "response_body": config_response.json() if config_response.ok else None,
                }
            )
        except Exception as e:
            print(f"Warning: Could not configure API: {e}")

    response = requests.get(f"{api_url}/data", timeout=30)
    response.raise_for_status()

    result = response.json()
    schema_version = result.get("meta", {}).get("schema_version", "unknown")

    audit_info["requests"].append(
        {
            "type": "GET",
            "url": f"{api_url}/data",
            "status_code": response.status_code,
            "response_body": result,
            "schema_version": schema_version,
        }
    )

    return result, audit_info


def _airflow_base_url() -> str:
    api_url = AIRFLOW_API_URL.rstrip("/")
    if "/api/" in api_url:
        return api_url.split("/api/", 1)[0]
    return api_url


def _get_airflow_token() -> str:
    base_url = _airflow_base_url()
    token_url = f"{base_url}/auth/token"

    response = requests.get(token_url, timeout=10)
    if response.ok:
        return response.json().get("access_token", "")

    response = requests.post(
        token_url,
        json={"username": AIRFLOW_API_USERNAME, "password": AIRFLOW_API_PASSWORD},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("access_token", "")


def _trigger_airflow_dag(bucket: str, key: str, correlation_id: str, inject_error: bool) -> None:
    payload = {
        "dag_run_id": f"trigger__{correlation_id}",
        "logical_date": datetime.utcnow().isoformat() + "Z",
        "conf": {
            "bucket": bucket,
            "key": key,
            "correlation_id": correlation_id,
            "inject_error": inject_error,
        },
    }
    token = _get_airflow_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.post(
        f"{AIRFLOW_API_URL}/dags/{AIRFLOW_DAG_ID}/dagRuns",
        json=payload,
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()


def lambda_handler(event, context):
    query_params = event.get("queryStringParameters") or {}
    inject_error = query_params.get("inject_error", "false").lower() == "true"

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    correlation_id = f"trigger-{timestamp}"
    s3_key = f"ingested/{timestamp}/data.json"
    audit_key = f"audit/{correlation_id}.json"

    if EXTERNAL_API_URL:
        try:
            data, audit_info = fetch_from_external_api(EXTERNAL_API_URL, inject_error)
            api_meta = data.get("meta", {})

            audit_payload = {
                "correlation_id": correlation_id,
                "timestamp": timestamp,
                "external_api_url": EXTERNAL_API_URL,
                "audit_info": audit_info,
            }
            s3_client.put_object(
                Bucket=LANDING_BUCKET,
                Key=audit_key,
                Body=json.dumps(audit_payload, indent=2),
                ContentType="application/json",
            )

            schema_version = api_meta.get("schema_version", "unknown")
        except Exception as e:
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": str(e), "correlation_id": correlation_id}),
            }
    else:
        if inject_error:
            data = {
                "data": [
                    {
                        "user_id": "user_12345",
                        "timestamp": timestamp,
                        "event_type": "click",
                        "raw_features": {"value": 150.0},
                    }
                ],
                "meta": {"schema_version": "2.0", "note": "Missing event_id"},
            }
        else:
            data = {
                "data": [
                    {
                        "event_id": "evt_001",
                        "user_id": "user_12345",
                        "timestamp": timestamp,
                        "event_type": "click",
                        "raw_features": {"value": 150.0},
                    }
                ],
                "meta": {"schema_version": "1.0"},
            }
        schema_version = data.get("meta", {}).get("schema_version", "unknown")
        audit_key = ""

    s3_metadata = {
        "correlation_id": correlation_id,
        "source": "trigger_lambda",
        "timestamp": timestamp,
        "schema_version": schema_version,
    }
    if audit_key:
        s3_metadata["audit_key"] = audit_key
    if inject_error:
        s3_metadata["schema_change_injected"] = "True"

    s3_client.put_object(
        Bucket=LANDING_BUCKET,
        Key=s3_key,
        Body=json.dumps(data, indent=2),
        ContentType="application/json",
        Metadata=s3_metadata,
    )

    _trigger_airflow_dag(LANDING_BUCKET, s3_key, correlation_id, inject_error)

    response_body = {
        "status": "triggered",
        "correlation_id": correlation_id,
        "s3_bucket": LANDING_BUCKET,
        "s3_key": s3_key,
        "inject_error": inject_error,
        "message": "Data written to S3. Airflow DAG run triggered.",
    }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response_body),
    }
