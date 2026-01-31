"""Mock DAG Lambda - Orchestration Placeholder.

Simulates what Prefect/Airflow would do:
1. Read data from S3 landing
2. Validate schema
3. Transform data
4. Write to S3 processed (or fail)

This Lambda is triggered automatically by S3 events when data lands.
"""

import json
import os
from datetime import datetime

import boto3

s3_client = boto3.client("s3")
REQUIRED_FIELDS = ["customer_id", "order_id", "amount", "timestamp"]


def lambda_handler(event, context):
    """Triggered by S3 upload to landing bucket."""
    processed_bucket = os.environ["PROCESSED_BUCKET"]

    # Get S3 object from event
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        print(f"Processing S3 object: s3://{bucket}/{key}")

        try:
            # Step 1: Read from S3
            print("Step 1: Reading from S3...")
            response = s3_client.get_object(Bucket=bucket, Key=key)
            data = json.loads(response["Body"].read().decode())

            # Extract correlation ID from metadata if present
            correlation_id = response.get("Metadata", {}).get("correlation_id", "unknown")
            print(f"Correlation ID: {correlation_id}")

            # Step 2: Validate schema
            print("Step 2: Validating schema...")
            validate_schema(data)
            print("✓ Schema validation passed")

            # Step 3: Transform
            print("Step 3: Transforming data...")
            transformed = transform_data(data)
            print("✓ Transformation completed")

            # Step 4: Write to processed
            output_key = key.replace("ingested/", "processed/")
            print(f"Step 4: Writing to s3://{processed_bucket}/{output_key}")
            s3_client.put_object(
                Bucket=processed_bucket,
                Key=output_key,
                Body=json.dumps(transformed, indent=2),
                ContentType="application/json",
                Metadata={
                    "correlation_id": correlation_id,
                    "source_key": key,
                    "processed_at": datetime.utcnow().isoformat(),
                },
            )

            print("✓ Pipeline completed successfully")
            return {"statusCode": 200, "message": "Success", "output_key": output_key}

        except ValueError as e:
            # Schema validation error
            print(f"✗ PIPELINE FAILED: {e}")
            print(f"S3 Key: {key}")
            print(f"Correlation ID: {correlation_id}")
            raise  # Let Lambda error appear in CloudWatch
        except Exception as e:
            # Other errors
            print(f"✗ PIPELINE FAILED: {e}")
            print(f"S3 Key: {key}")
            raise


def validate_schema(data):
    """Validate schema has required fields."""
    records = data.get("data", [])
    if not records:
        raise ValueError("No data records found")

    for i, record in enumerate(records):
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        if missing:
            raise ValueError(
                f"Schema validation failed: Missing fields {missing} in record {i}"
            )


def transform_data(data):
    """Transform data (convert amount to cents)."""
    records = data.get("data", [])
    for record in records:
        record["amount_cents"] = int(float(record["amount"]) * 100)
    return data
