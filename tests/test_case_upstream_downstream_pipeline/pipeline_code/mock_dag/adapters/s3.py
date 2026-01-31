import json
from datetime import UTC, datetime

import boto3

from .errors import SystemError

s3_client = boto3.client("s3")


def read_json(bucket: str, key: str) -> tuple[dict, str]:
    """Read JSON from S3 and return data and correlation_id."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        data = json.loads(response["Body"].read().decode())
        correlation_id = response.get("Metadata", {}).get("correlation_id", "unknown")
        return data, correlation_id
    except Exception as e:
        raise SystemError(f"Failed to read from S3: {e}") from e


def write_json(bucket: str, key: str, data: dict, correlation_id: str, source_key: str):
    """Write JSON to S3 with metadata."""
    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(data, indent=2),
            ContentType="application/json",
            Metadata={
                "correlation_id": correlation_id,
                "source_key": source_key,
                "processed_at": datetime.now(UTC).isoformat(),
            },
        )
    except Exception as e:
        raise SystemError(f"Failed to write to S3: {e}") from e
