"""
Lambda handler for /trigger endpoint.

Endpoints:
- POST /trigger - Run pipeline with valid data (happy path)
- POST /trigger?inject_error=true - Run pipeline with schema error (failed path)

This Lambda:
1. Fetches data from external vendor API
2. Stores audit payload with vendor request/response
3. Writes data to S3 landing bucket with audit_key in metadata
4. Starts ECS Flink task via RunTask API
5. Returns correlation_id and task ARN
"""

import json
import os
from datetime import datetime

import boto3
import requests

# Environment variables
LANDING_BUCKET = os.environ.get("LANDING_BUCKET", "")
PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET", "")
EXTERNAL_API_URL = os.environ.get("EXTERNAL_API_URL", "")
ECS_CLUSTER = os.environ.get("ECS_CLUSTER", "")
TASK_DEFINITION = os.environ.get("TASK_DEFINITION", "")
SUBNET_IDS = os.environ.get("SUBNET_IDS", "").split(",")
SECURITY_GROUP_ID = os.environ.get("SECURITY_GROUP_ID", "")

s3_client = boto3.client("s3")
ecs_client = boto3.client("ecs")


def fetch_from_external_api(api_url: str, inject_error: bool = False) -> tuple[dict, dict]:
    """Fetch data from external API with audit tracking."""
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


def start_flink_task(correlation_id: str, s3_key: str) -> str:
    """Start ECS Flink task and return task ARN."""
    response = ecs_client.run_task(
        cluster=ECS_CLUSTER,
        taskDefinition=TASK_DEFINITION,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNET_IDS,
                "securityGroups": [SECURITY_GROUP_ID],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": "FlinkContainer",
                    "environment": [
                        {"name": "CORRELATION_ID", "value": correlation_id},
                        {"name": "S3_KEY", "value": s3_key},
                    ],
                }
            ]
        },
    )

    if not response.get("tasks"):
        failures = response.get("failures", [])
        raise RuntimeError(f"Failed to start ECS task: {failures}")

    task_arn = response["tasks"][0]["taskArn"]
    print(f"Started ECS Flink task: {task_arn}")
    return task_arn


def lambda_handler(event, context):
    """Handle API Gateway requests to trigger pipeline."""
    # Parse query parameters
    query_params = event.get("queryStringParameters") or {}
    inject_error = query_params.get("inject_error", "false").lower() == "true"

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    correlation_id = f"flink-{timestamp}"
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
        # Fallback to generated test data (ML event format)
        if inject_error:
            # Schema v2.0: Missing event_id (breaking change for ML pipeline)
            data = {
                "data": [
                    {
                        "user_id": "user_12345",
                        "timestamp": timestamp,
                        "event_type": "click",
                        "raw_features": {
                            "value": 150.0,
                            "duration": 45,
                            "count": 3,
                            "is_weekend": 0,
                            "hour": 14,
                        },
                    },
                    {
                        "user_id": "user_67890",
                        "timestamp": timestamp,
                        "event_type": "purchase",
                        "raw_features": {
                            "value": 299.99,
                            "duration": 120,
                            "count": 1,
                            "is_weekend": 1,
                            "hour": 18,
                        },
                    },
                ],
                "meta": {"schema_version": "2.0", "note": "BREAKING: event_id field removed"},
            }
        else:
            # Schema v1.0: Complete event schema with event_id
            data = {
                "data": [
                    {
                        "event_id": "evt_001",
                        "user_id": "user_12345",
                        "timestamp": timestamp,
                        "event_type": "click",
                        "raw_features": {
                            "value": 150.0,
                            "duration": 45,
                            "count": 3,
                            "is_weekend": 0,
                            "hour": 14,
                        },
                    },
                    {
                        "event_id": "evt_002",
                        "user_id": "user_67890",
                        "timestamp": timestamp,
                        "event_type": "purchase",
                        "raw_features": {
                            "value": 299.99,
                            "duration": 120,
                            "count": 1,
                            "is_weekend": 1,
                            "hour": 18,
                        },
                    },
                ],
                "meta": {"schema_version": "1.0"},
            }
        schema_version = data.get("meta", {}).get("schema_version", "unknown")
        audit_key = ""

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

    # Start ECS Flink task
    task_arn = None
    if ECS_CLUSTER and TASK_DEFINITION:
        try:
            task_arn = start_flink_task(correlation_id, s3_key)
        except Exception as e:
            print(f"ERROR: Failed to start ECS task: {e}")
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {
                        "error": str(e),
                        "correlation_id": correlation_id,
                        "s3_key": s3_key,
                    }
                ),
            }
    else:
        print("ECS_CLUSTER or TASK_DEFINITION not configured, skipping task launch")

    response_body = {
        "status": "triggered",
        "correlation_id": correlation_id,
        "s3_bucket": LANDING_BUCKET,
        "s3_key": s3_key,
        "audit_key": audit_key,
        "task_arn": task_arn,
        "inject_error": inject_error,
        "message": "Data written to S3 and Flink task started.",
    }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response_body),
    }
