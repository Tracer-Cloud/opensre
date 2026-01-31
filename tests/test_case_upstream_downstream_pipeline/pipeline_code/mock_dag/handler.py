"""
Upstream/Downstream Pipeline - Core Processing Logic.

Follows the 'Pure Business Logic' principle:
1. Infrastructure (S3) is handled only at the edges.
2. Business logic (Validation, Transformation) is pure and silent.
3. No verbose orchestration logging or manual alert structuring.
"""

import json
import os
from datetime import UTC, datetime
import boto3

try:
    from config import LANDING_BUCKET, PIPELINE_NAME, PROCESSED_BUCKET, REQUIRED_FIELDS
except ImportError:
    LANDING_BUCKET = os.environ.get("LANDING_BUCKET", "")
    PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET", "")
    PIPELINE_NAME = "upstream_downstream_pipeline"
    REQUIRED_FIELDS = ["customer_id", "order_id", "amount", "timestamp"]

s3_client = boto3.client("s3")

# --- Core Business Logic (Pure) ---

def validate_records(records: list[dict]):
    if not records:
        raise ValueError("No data records found")

    for i, record in enumerate(records):
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        if missing:
            raise ValueError(f"Schema validation failed: Missing fields {missing} in record {i}")

def transform_records(records: list[dict]) -> list[dict]:
    for record in records:
        record["amount_cents"] = int(float(record["amount"]) * 100)
    return records

# --- Infrastructure Adapters ---

def lambda_handler(event, context):
    """Orchestration layer: Adapts S3 events to Business Logic."""
    correlation_id = "unknown"
    
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        
        # 1. Extraction
        response = s3_client.get_object(Bucket=bucket, Key=key)
        raw_payload = json.loads(response["Body"].read().decode())
        correlation_id = response.get("Metadata", {}).get("correlation_id", "unknown")
        
        records = raw_payload.get("data", [])

        # 2. Processing (Business Logic)
        validate_records(records)
        transformed_records = transform_records(records)

        # 3. Loading
        output_key = key.replace("ingested/", "processed/")
        s3_client.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=output_key,
            Body=json.dumps({"data": transformed_records}, indent=2),
            ContentType="application/json",
            Metadata={
                "correlation_id": correlation_id,
                "source_key": key,
                "processed_at": datetime.now(UTC).isoformat(),
            },
        )

    return {"status": "success", "correlation_id": correlation_id}
