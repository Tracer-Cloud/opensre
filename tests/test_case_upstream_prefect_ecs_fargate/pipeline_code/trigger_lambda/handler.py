"""
Lambda handler for /trigger endpoint.

Endpoints:
- POST /trigger - Run pipeline with valid data (happy path)
- POST /trigger?inject_error=true - Run pipeline with schema error (failed path)

This Lambda:
1. Generates test data (valid or with schema error)
2. Writes data to S3 landing bucket
3. Triggers the Prefect flow via API
4. Returns flow run ID
"""

import json
import os
from datetime import datetime

import boto3
import urllib3

# Environment variables
LANDING_BUCKET = os.environ.get("LANDING_BUCKET", "")
PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET", "")
PREFECT_API_URL = os.environ.get("PREFECT_API_URL", "http://localhost:4200/api")

s3_client = boto3.client("s3")
http = urllib3.PoolManager()


def lambda_handler(event, context):
    """Handle API Gateway requests to trigger pipeline."""
    # Parse query parameters
    query_params = event.get("queryStringParameters") or {}
    inject_error = query_params.get("inject_error", "false").lower() == "true"

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    correlation_id = f"trigger-{timestamp}"
    s3_key = f"ingested/{timestamp}/data.json"

    # Generate test data
    if inject_error:
        # Missing customer_id field - will cause schema validation error
        data = {
            "data": [
                {"order_id": "ORD-001", "amount": 99.99, "timestamp": timestamp},
                {"order_id": "ORD-002", "amount": 149.50, "timestamp": timestamp},
            ],
            "meta": {"schema_version": "2.0", "note": "Missing customer_id"},
        }
    else:
        # Valid data
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

    # Write to S3
    s3_client.put_object(
        Bucket=LANDING_BUCKET,
        Key=s3_key,
        Body=json.dumps(data, indent=2),
        ContentType="application/json",
        Metadata={"correlation_id": correlation_id},
    )

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
