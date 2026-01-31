"""API Ingester Lambda - Fetches data from external API and writes to S3.

This Lambda:
1. Calls Mock External API to fetch data
2. Writes raw data to S3 landing bucket
3. S3 event automatically triggers Mock DAG Lambda

No Airflow triggering - using S3 events instead.
"""

import json
import os
from datetime import datetime
from typing import Any

import boto3
import requests

s3_client = boto3.client("s3")


def fetch_from_external_api(
    api_url: str, inject_schema_change: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch data from external API.

    Args:
        api_url: External API base URL
        inject_schema_change: If True, configure API to inject schema change

    Returns:
        Tuple of (API response data, audit info with request/response details)
    """
    audit_info = {"requests": []}

    if inject_schema_change:
        try:
            config_response = requests.post(
                f"{api_url}/config",
                json={"inject_schema_change": True},
                timeout=10,
            )
            print("Configured external API to inject schema change")
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
    print(f"Fetched from external API: schema_version={schema_version}")

    # Log structured request/response for audit
    audit_info["requests"].append(
        {
            "type": "GET",
            "url": f"{api_url}/data",
            "status_code": response.status_code,
            "response_body": result,
            "schema_version": schema_version,
        }
    )
    print(f"EXTERNAL_API_AUDIT: {json.dumps(audit_info)}")

    return result, audit_info


def lambda_handler(event: dict, context: Any) -> dict:
    """Lambda handler for API ingestion.

    Supports both direct Lambda invoke and API Gateway triggers.

    Event parameters:
    - inject_schema_change: bool - If true, API returns bad schema
    - correlation_id: str - Optional correlation ID for tracing

    Returns:
        dict with s3_key, bucket, and status
    """
    # Handle API Gateway event format
    if "body" in event and isinstance(event.get("body"), str):
        try:
            body = json.loads(event["body"])
            inject_schema_change = body.get("inject_schema_change", False)
            correlation_id = (
                body.get("correlation_id") or f"ing-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            )
        except json.JSONDecodeError:
            inject_schema_change = False
            correlation_id = f"ing-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    else:
        # Direct Lambda invoke
        inject_schema_change = event.get("inject_schema_change", False)
        correlation_id = (
            event.get("correlation_id") or f"ing-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        )

    landing_bucket = os.environ.get("LANDING_BUCKET")
    external_api_url = os.environ.get("EXTERNAL_API_URL")

    if not landing_bucket:
        return {
            "statusCode": 500,
            "error": "LANDING_BUCKET environment variable not set",
        }

    if not external_api_url:
        return {
            "statusCode": 500,
            "error": "EXTERNAL_API_URL environment variable not set",
        }

    print(f"Correlation ID: {correlation_id}")

    # Fetch data from external API
    try:
        api_response, audit_info = fetch_from_external_api(external_api_url, inject_schema_change)
        data = api_response.get("data", [])
        api_meta = api_response.get("meta", {})
        print(f"Fetched {len(data)} records from external API")
    except Exception as e:
        print(f"ERROR: External API call failed: {e}")
        return {
            "statusCode": 500,
            "error": f"External API call failed: {str(e)}",
            "correlation_id": correlation_id,
        }

    # Write to S3
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    s3_key = f"ingested/{timestamp}/data.json"
    audit_key = f"audit/{correlation_id}.json"

    try:
        # Write audit object with external API request/response details
        audit_payload = {
            "correlation_id": correlation_id,
            "timestamp": timestamp,
            "external_api_url": external_api_url,
            "audit_info": audit_info,
        }
        s3_client.put_object(
            Bucket=landing_bucket,
            Key=audit_key,
            Body=json.dumps(audit_payload, indent=2),
            ContentType="application/json",
        )
        print(f"Wrote audit data to S3: s3://{landing_bucket}/{audit_key}")

        # Write main data with audit_key in metadata
        s3_client.put_object(
            Bucket=landing_bucket,
            Key=s3_key,
            Body=json.dumps(api_response, indent=2),
            ContentType="application/json",
            Metadata={
                "correlation_id": correlation_id,
                "source": "api_ingester_lambda",
                "timestamp": timestamp,
                "schema_version": api_meta.get("schema_version", "unknown"),
                "schema_change_injected": str(inject_schema_change),
                "audit_key": audit_key,
            },
        )
        print(f"Wrote data to S3: s3://{landing_bucket}/{s3_key}")
        print(
            f"Metadata: correlation_id={correlation_id}, schema_version={api_meta.get('schema_version')}, audit_key={audit_key}"
        )
    except Exception as e:
        print(f"ERROR: S3 write failed: {e}")
        return {
            "statusCode": 500,
            "error": f"S3 write failed: {str(e)}",
            "correlation_id": correlation_id,
        }

    result = {
        "statusCode": 200,
        "s3_key": s3_key,
        "s3_bucket": landing_bucket,
        "record_count": len(data),
        "correlation_id": correlation_id,
        "schema_version": api_meta.get("schema_version"),
        "schema_change_injected": inject_schema_change,
    }

    # Format for API Gateway if called via HTTP
    if "body" in event:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }

    return result
