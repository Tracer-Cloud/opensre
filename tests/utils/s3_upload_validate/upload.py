"""S3 upload and validation utilities."""

import json
from datetime import UTC, datetime
from typing import NamedTuple

import boto3
from botocore.exceptions import ClientError


class TestData(NamedTuple):
    """Test data uploaded to S3."""

    key: str
    correlation_id: str


def upload_test_data(
    bucket: str,
    payload: dict,
    s3_client=None,
) -> TestData:
    """Upload test data to S3 bucket."""
    s3 = s3_client or boto3.client("s3")
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    key = f"ingested/{timestamp}/data.json"
    correlation_id = f"local-test-{timestamp}"

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload),
        ContentType="application/json",
        Metadata={"correlation_id": correlation_id},
    )

    print(f"📤 Uploaded: s3://{bucket}/{key}")
    return TestData(key, correlation_id)


def verify_output(
    bucket: str,
    input_key: str,
    s3_client=None,
) -> bool:
    """Verify processed output exists in S3."""
    s3 = s3_client or boto3.client("s3")
    output_key = input_key.replace("ingested/", "processed/")

    try:
        response = s3.get_object(Bucket=bucket, Key=output_key)
        data = json.loads(response["Body"].read())
        record_count = len(data.get("data", []))

        print(f"✓ Verified: s3://{bucket}/{output_key} ({record_count} records)")
        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            print(f"✗ Missing: s3://{bucket}/{output_key}")
        else:
            print(f"✗ Error: {e}")
        return False


TEST_TIMESTAMP = "20260101-120000"

# Fixed payloads for local testing
VALID_PAYLOAD = {
    "data": [
        {
            "customer_id": "CUST-001",
            "order_id": "ORD-001",
            "amount": 99.99,
            "timestamp": TEST_TIMESTAMP,
        },
        {
            "customer_id": "CUST-002",
            "order_id": "ORD-002",
            "amount": 149.50,
            "timestamp": TEST_TIMESTAMP,
        },
    ]
}

INVALID_PAYLOAD = {
    "data": [
        {"order_id": "ORD-001", "amount": 99.99, "timestamp": TEST_TIMESTAMP},
    ]
}
