"""
ECS Fargate Airflow Test Case - Failure Injection Utilities.

Provides functions to programmatically trigger various failure scenarios
for testing the agent's root cause analysis capabilities.

Failure Scenarios:
1. Schema change - Missing customer_id field (simulates upstream API change)
2. Empty data - No records in ingested data
3. Invalid format - Malformed JSON data
"""

import json
import os
from datetime import datetime
from typing import Any

try:
    import boto3
except ImportError:
    boto3 = None


def inject_schema_change(
    bucket: str | None = None,
    key_prefix: str = "ingested",
) -> dict[str, Any]:
    """
    Inject a schema change by writing data without customer_id field.

    This simulates an upstream API changing its response format,
    which causes the downstream Airflow DAG to fail schema validation.

    Args:
        bucket: S3 bucket name (defaults to DATA_BUCKET env var)
        key_prefix: S3 key prefix for data files

    Returns:
        dict with s3_key and injection details
    """
    if boto3 is None:
        raise RuntimeError("boto3 not available")

    bucket = bucket or os.getenv("DATA_BUCKET", "tracer-test-data")
    s3 = boto3.client("s3")

    # Data WITHOUT customer_id field (schema violation)
    bad_data = [
        {
            "order_id": "ORD-BAD-001",
            "amount": 99.99,
            "timestamp": datetime.utcnow().isoformat(),
            # Missing: customer_id
        },
        {
            "order_id": "ORD-BAD-002",
            "amount": 149.50,
            "timestamp": datetime.utcnow().isoformat(),
            # Missing: customer_id
        },
    ]

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    s3_key = f"{key_prefix}/{timestamp}/data.json"

    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=json.dumps(bad_data, indent=2),
        ContentType="application/json",
    )

    return {
        "type": "schema_change",
        "bucket": bucket,
        "s3_key": s3_key,
        "missing_field": "customer_id",
        "record_count": len(bad_data),
        "description": "Data written without customer_id field to simulate upstream API schema change",
    }


def inject_empty_data(
    bucket: str | None = None,
    key_prefix: str = "ingested",
) -> dict[str, Any]:
    """
    Inject empty data file.

    This simulates a scenario where the upstream data source returns no records.

    Args:
        bucket: S3 bucket name
        key_prefix: S3 key prefix

    Returns:
        dict with s3_key and injection details
    """
    if boto3 is None:
        raise RuntimeError("boto3 not available")

    bucket = bucket or os.getenv("DATA_BUCKET", "tracer-test-data")
    s3 = boto3.client("s3")

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    s3_key = f"{key_prefix}/{timestamp}/data.json"

    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=json.dumps([], indent=2),
        ContentType="application/json",
    )

    return {
        "type": "empty_data",
        "bucket": bucket,
        "s3_key": s3_key,
        "record_count": 0,
        "description": "Empty data file to simulate no records from upstream",
    }


def inject_malformed_json(
    bucket: str | None = None,
    key_prefix: str = "ingested",
) -> dict[str, Any]:
    """
    Inject malformed JSON data.

    This simulates a scenario where the data file is corrupted or invalid.

    Args:
        bucket: S3 bucket name
        key_prefix: S3 key prefix

    Returns:
        dict with s3_key and injection details
    """
    if boto3 is None:
        raise RuntimeError("boto3 not available")

    bucket = bucket or os.getenv("DATA_BUCKET", "tracer-test-data")
    s3 = boto3.client("s3")

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    s3_key = f"{key_prefix}/{timestamp}/data.json"

    # Invalid JSON content
    malformed_content = '{"order_id": "ORD-001", "amount": 99.99, "invalid json here'

    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=malformed_content,
        ContentType="application/json",
    )

    return {
        "type": "malformed_json",
        "bucket": bucket,
        "s3_key": s3_key,
        "description": "Malformed JSON to simulate data corruption",
    }


def write_valid_data(
    bucket: str | None = None,
    key_prefix: str = "ingested",
) -> dict[str, Any]:
    """
    Write valid data for successful pipeline run.

    Use this to reset to a known good state.

    Args:
        bucket: S3 bucket name
        key_prefix: S3 key prefix

    Returns:
        dict with s3_key and data details
    """
    if boto3 is None:
        raise RuntimeError("boto3 not available")

    bucket = bucket or os.getenv("DATA_BUCKET", "tracer-test-data")
    s3 = boto3.client("s3")

    # Valid data with all required fields
    valid_data = [
        {
            "customer_id": "CUST-001",
            "order_id": "ORD-001",
            "amount": 99.99,
            "timestamp": datetime.utcnow().isoformat(),
        },
        {
            "customer_id": "CUST-002",
            "order_id": "ORD-002",
            "amount": 149.50,
            "timestamp": datetime.utcnow().isoformat(),
        },
        {
            "customer_id": "CUST-003",
            "order_id": "ORD-003",
            "amount": 75.00,
            "timestamp": datetime.utcnow().isoformat(),
        },
    ]

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    s3_key = f"{key_prefix}/{timestamp}/data.json"

    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=json.dumps(valid_data, indent=2),
        ContentType="application/json",
    )

    return {
        "type": "valid_data",
        "bucket": bucket,
        "s3_key": s3_key,
        "record_count": len(valid_data),
        "description": "Valid data with all required fields",
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python trigger_failure.py <scenario>")
        print("Scenarios: schema_change, empty_data, malformed_json, valid")
        sys.exit(1)

    scenario = sys.argv[1]

    if scenario == "schema_change":
        result = inject_schema_change()
    elif scenario == "empty_data":
        result = inject_empty_data()
    elif scenario == "malformed_json":
        result = inject_malformed_json()
    elif scenario == "valid":
        result = write_valid_data()
    else:
        print(f"Unknown scenario: {scenario}")
        sys.exit(1)

    print(json.dumps(result, indent=2))
