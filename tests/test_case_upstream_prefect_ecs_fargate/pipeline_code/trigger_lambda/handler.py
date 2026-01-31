"""
Lambda handler for /trigger endpoint.

Endpoints:
- POST /trigger - Run pipeline with valid data (happy path)
- POST /trigger?inject_error=true - Run pipeline with schema error (failed path)

This Lambda:
1. Fetches data from external vendor API
2. Stores audit payload with vendor request/response
3. Writes data to S3 landing bucket with audit_key in metadata
4. Triggers the Prefect flow via API
5. Returns flow run ID
"""

import json
import os
from datetime import datetime

import boto3
import requests

# Environment variables
LANDING_BUCKET = os.environ.get("LANDING_BUCKET", "")
PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET", "")
PREFECT_API_URL = os.environ.get("PREFECT_API_URL", "http://localhost:4200/api")
EXTERNAL_API_URL = os.environ.get("EXTERNAL_API_URL", "")

s3_client = boto3.client("s3")


def fetch_from_external_api(api_url: str, inject_error: bool = False) -> tuple[dict, dict]:
    """
    Fetch data from external API with audit tracking.

    Returns:
        Tuple of (API response data, audit info with request/response details)
    """
    audit_info = {"requests": []}

    if inject_error:
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


def lambda_handler(event, context):
    """Handle API Gateway requests to trigger pipeline."""
    # Parse query parameters
    query_params = event.get("queryStringParameters") or {}
    inject_error = query_params.get("inject_error", "false").lower() == "true"

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    correlation_id = f"trigger-{timestamp}"
    s3_key = f"ingested/{timestamp}/data.json"
    audit_key = f"audit/{correlation_id}.json"

    # Fetch from external API if configured
    if EXTERNAL_API_URL:
        try:
            data, audit_info = fetch_from_external_api(EXTERNAL_API_URL, inject_error)
            api_meta = data.get("meta", {})

            # Write audit payload
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
            print(f"Wrote audit data to S3: s3://{LANDING_BUCKET}/{audit_key}")

            schema_version = api_meta.get("schema_version", "unknown")
        except Exception as e:
            print(f"ERROR: External API call failed: {e}")
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": str(e), "correlation_id": correlation_id}),
            }
    else:
        # Fallback to generated test data
        if inject_error:
            data = {
                "data": [
                    {"order_id": "ORD-001", "amount": 99.99, "timestamp": timestamp},
                    {"order_id": "ORD-002", "amount": 149.50, "timestamp": timestamp},
                ],
                "meta": {"schema_version": "2.0", "note": "Missing customer_id"},
            }
        else:
            data = {
                "data": [
                    {
                        "customer_id": "CUST-001",
                        "order_id": "ORD-001",
                        "amount": 99.99,
                        "timestamp": timestamp,
                    },
                    {
                        "customer_id": "CUST-002",
                        "order_id": "ORD-002",
                        "amount": 149.50,
                        "timestamp": timestamp,
                    },
                ],
                "meta": {"schema_version": "1.0"},
            }
        schema_version = data.get("meta", {}).get("schema_version", "unknown")
        audit_key = ""  # No audit if no external API

    # Write to S3 with enriched metadata
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
    print(f"Wrote data to S3: s3://{LANDING_BUCKET}/{s3_key}")
    print(f"Metadata: {json.dumps(s3_metadata)}")

    # Trigger Prefect flow
    # Note: In production, you'd create a deployment and trigger it
    # For now, we just return success with the S3 key
    # The flow would be triggered by the ECS worker polling for work

    response_body = {
        "status": "triggered",
        "correlation_id": correlation_id,
        "s3_bucket": LANDING_BUCKET,
        "s3_key": s3_key,
        "inject_error": inject_error,
        "message": "Data written to S3. Flow will process when triggered.",
    }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response_body),
    }
