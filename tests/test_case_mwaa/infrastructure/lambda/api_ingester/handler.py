"""
API Ingester Lambda function.

Fetches data from an external API (or generates mock data) and writes to S3.
Can inject schema changes to test failure scenarios.
"""

import base64
import json
import os
from datetime import datetime
from typing import Any

import boto3
import requests


def fetch_from_external_api(api_url: str, inject_schema_change: bool = False) -> dict[str, Any]:
    """
    Fetch data from external API.

    Args:
        api_url: External API base URL
        inject_schema_change: If True, configure API to inject schema change

    Returns:
        API response data
    """
    if inject_schema_change:
        try:
            requests.post(
                f"{api_url}/config",
                json={"inject_schema_change": True},
                timeout=10,
            )
            print(f"Configured external API to inject schema change")
        except Exception as e:
            print(f"Warning: Could not configure API: {e}")

    response = requests.get(f"{api_url}/data", timeout=30)
    response.raise_for_status()

    result = response.json()
    print(f"Fetched from external API: schema_version={result.get('meta', {}).get('schema_version')}")

    return result


def get_mock_api_data(inject_schema_change: bool = False) -> list[dict[str, Any]]:
    """
    Generate mock API data when external API is not available.

    Args:
        inject_schema_change: If True, omit 'customer_id' field to simulate
                              an upstream API schema change.
    """
    base_data = [
        {
            "order_id": "ORD-001",
            "amount": 99.99,
            "timestamp": datetime.utcnow().isoformat(),
        },
        {
            "order_id": "ORD-002",
            "amount": 149.50,
            "timestamp": datetime.utcnow().isoformat(),
        },
        {
            "order_id": "ORD-003",
            "amount": 75.00,
            "timestamp": datetime.utcnow().isoformat(),
        },
    ]

    if inject_schema_change:
        print("INJECTING SCHEMA CHANGE: Omitting customer_id field")
        return base_data

    for i, record in enumerate(base_data):
        record["customer_id"] = f"CUST-{i + 1:03d}"

    return base_data


def write_to_s3(data: list[dict], bucket: str, key: str) -> dict:
    """Write data to S3."""
    s3 = boto3.client("s3")

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2),
        ContentType="application/json",
    )

    return {
        "bucket": bucket,
        "key": key,
        "record_count": len(data),
    }


def trigger_mwaa_dag(
    environment_name: str,
    dag_id: str,
    conf: dict[str, Any],
) -> dict:
    """Trigger MWAA DAG via CLI token."""
    mwaa = boto3.client("mwaa")

    token_response = mwaa.create_cli_token(Name=environment_name)
    cli_token = token_response["CliToken"]
    hostname = token_response["WebServerHostname"]

    conf_json = json.dumps(conf)
    command = f"dags trigger {dag_id} --conf '{conf_json}'"

    response = requests.post(
        f"https://{hostname}/aws_mwaa/cli",
        headers={
            "Authorization": f"Bearer {cli_token}",
            "Content-Type": "text/plain",
        },
        data=command,
        timeout=30,
    )

    if response.status_code == 200:
        result = response.json()
        stdout = base64.b64decode(result.get("stdout", "")).decode()
        stderr = base64.b64decode(result.get("stderr", "")).decode()
        return {
            "success": True,
            "stdout": stdout,
            "stderr": stderr,
        }

    return {
        "success": False,
        "error": f"HTTP {response.status_code}: {response.text}",
    }


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda handler for API ingestion.

    Event parameters:
    - inject_schema_change: bool - If true, simulate API schema change
    - trigger_dag: bool - If true, trigger MWAA DAG after S3 write
    - use_external_api: bool - If true, call external API (default: True if URL set)

    Returns:
        dict with s3_key and optional dag_trigger_result
    """
    inject_schema_change = event.get("inject_schema_change", False)
    trigger_dag = event.get("trigger_dag", True)

    data_bucket = os.environ.get("DATA_BUCKET")
    mwaa_environment = os.environ.get("MWAA_ENVIRONMENT")
    dag_id = os.environ.get("DAG_ID", "ingest_transform")
    external_api_url = os.environ.get("EXTERNAL_API_URL")

    # Determine whether to use external API
    use_external_api = event.get("use_external_api", bool(external_api_url))

    if not data_bucket:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "DATA_BUCKET environment variable not set"}),
        }

    # Fetch data from external API or generate mock data
    data_source = "mock"
    api_meta = None

    if use_external_api and external_api_url:
        try:
            api_response = fetch_from_external_api(external_api_url, inject_schema_change)
            data = api_response.get("data", [])
            api_meta = api_response.get("meta", {})
            data_source = "external_api"
            print(f"Using data from external API: {len(data)} records")
        except Exception as e:
            print(f"External API call failed: {e}, falling back to mock data")
            data = get_mock_api_data(inject_schema_change=inject_schema_change)
            data_source = "mock_fallback"
    else:
        data = get_mock_api_data(inject_schema_change=inject_schema_change)

    # Write to S3
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    s3_key = f"ingested/{timestamp}/data.json"

    s3_result = write_to_s3(data, data_bucket, s3_key)
    print(f"Wrote data to S3: {s3_result}")

    result = {
        "statusCode": 200,
        "s3_key": s3_key,
        "s3_bucket": data_bucket,
        "record_count": len(data),
        "schema_change_injected": inject_schema_change,
        "data_source": data_source,
        "api_meta": api_meta,
    }

    # Optionally trigger MWAA DAG
    if trigger_dag and mwaa_environment:
        dag_conf = {"s3_key": s3_key}
        dag_result = trigger_mwaa_dag(mwaa_environment, dag_id, dag_conf)
        result["dag_trigger"] = dag_result
        print(f"DAG trigger result: {dag_result}")

    return result
