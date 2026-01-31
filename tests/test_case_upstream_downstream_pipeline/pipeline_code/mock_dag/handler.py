"""Mock DAG Lambda - Orchestration Placeholder.

Simulates orchestration workflow with clear step separation:
1. Read from S3
2. Validate schema
3. Transform data
4. Write to S3

On failure, logs structured alert data for agent investigation.
"""

import json
import os
import traceback
from datetime import UTC, datetime

import boto3

try:
    from config import LANDING_BUCKET, PIPELINE_NAME, PROCESSED_BUCKET, REQUIRED_FIELDS
except ImportError:
    # Fallback to environment variables if config.py not available
    LANDING_BUCKET = os.environ.get("LANDING_BUCKET", "")
    PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET", "")
    PIPELINE_NAME = "upstream_downstream_pipeline"
    REQUIRED_FIELDS = ["customer_id", "order_id", "amount", "timestamp"]

s3_client = boto3.client("s3")


def step_1_read_from_s3(bucket: str, key: str, correlation_id: str) -> dict:
    """Step 1: Read data from S3 landing bucket."""
    print(f"[{correlation_id}] Step 1: Reading from S3...")
    print(f"  Bucket: {bucket}")
    print(f"  Key: {key}")

    response = s3_client.get_object(Bucket=bucket, Key=key)
    data = json.loads(response["Body"].read().decode())

    print(f"  Records: {len(data.get('data', []))}")
    return data


def step_2_validate_schema(data: dict, correlation_id: str) -> None:
    """Step 2: Validate schema has required fields."""
    print(f"[{correlation_id}] Step 2: Validating schema...")
    print(f"  Required fields: {REQUIRED_FIELDS}")

    records = data.get("data", [])
    if not records:
        raise ValueError("No data records found")

    for i, record in enumerate(records):
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        if missing:
            raise ValueError(
                f"Schema validation failed: Missing fields {missing} in record {i}"
            )

    print(f"  ✓ All {len(records)} records valid")


def step_3_transform_data(data: dict, correlation_id: str) -> dict:
    """Step 3: Transform data (convert amount to cents)."""
    print(f"[{correlation_id}] Step 3: Transforming data...")

    records = data.get("data", [])
    for record in records:
        record["amount_cents"] = int(float(record["amount"]) * 100)

    print(f"  ✓ Transformed {len(records)} records")
    return data


def step_4_write_to_s3(
    data: dict, bucket: str, key: str, correlation_id: str, source_key: str
) -> str:
    """Step 4: Write processed data to S3."""
    print(f"[{correlation_id}] Step 4: Writing to S3...")
    print(f"  Bucket: {bucket}")
    print(f"  Key: {key}")

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

    print(f"  ✓ Written to s3://{bucket}/{key}")
    return key


def log_structured_alert(
    error: Exception,
    error_traceback: str,
    pipeline_name: str,
    run_id: str,
    s3_key: str,
    bucket: str,
    correlation_id: str,
) -> None:
    """Log structured alert data for agent investigation."""
    alert_data = {
        "alert_type": "pipeline_failure",
        "alert_id": f"alert-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now(UTC).isoformat(),
        "pipeline_name": pipeline_name,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "status": "failed",
        "annotations": {
            "s3_bucket": bucket,
            "s3_key": s3_key,
            "error": str(error),
            "error_type": type(error).__name__,
            "pipeline_step": "schema_validation",
            "context_sources": "s3,lambda",
        },
    }

    print(f"\n{'='*60}")
    print("ALERT: Pipeline Failure Detected")
    print(f"{'='*60}")
    print(json.dumps(alert_data, indent=2))
    print(f"{'='*60}\n")


def lambda_handler(event, context):
    """Lambda handler - triggered by S3 upload to landing bucket."""
    # Get S3 object from event
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

        print(f"Processing S3 object: s3://{bucket}/{key}")

        try:
            # Get metadata
            metadata_response = s3_client.head_object(Bucket=bucket, Key=key)
            correlation_id = metadata_response.get("Metadata", {}).get(
                "correlation_id", "unknown"
            )
            print(f"Correlation ID: {correlation_id}")

            # Step 1: Read from S3
            data = step_1_read_from_s3(bucket, key, correlation_id)

            # Step 2: Validate schema
            step_2_validate_schema(data, correlation_id)

            # Step 3: Transform
            transformed = step_3_transform_data(data, correlation_id)

            # Step 4: Write to processed
            output_key = key.replace("ingested/", "processed/")
            step_4_write_to_s3(
                transformed, PROCESSED_BUCKET, output_key, correlation_id, key
            )

            print(f"[{correlation_id}] ✓ Pipeline completed successfully")
            return {
                "statusCode": 200,
                "message": "Success",
                "output_key": output_key,
                "correlation_id": correlation_id,
            }

        except ValueError as e:
            # Schema validation error - log structured alert
            error_traceback = traceback.format_exc()

            print(f"\n[{correlation_id}] ✗ PIPELINE FAILED")
            print(f"  Error: {e}")
            print(f"  S3 Key: {key}")
            print(f"  Bucket: {bucket}")

            # Log structured alert data
            log_structured_alert(
                error=e,
                error_traceback=error_traceback,
                pipeline_name=PIPELINE_NAME,
                run_id=run_id,
                s3_key=key,
                bucket=bucket,
                correlation_id=correlation_id,
            )

            # Re-raise to mark Lambda as failed
            raise

        except Exception as e:
            # Other errors
            print(f"\n[{correlation_id}] ✗ PIPELINE FAILED: {e}")
            print(f"  S3 Key: {key}")
            raise
