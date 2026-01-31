#!/usr/bin/env python3
"""PyFlink batch job for processing data from S3.

This job:
1. Reads input data from S3 landing bucket
2. Validates schema (requires customer_id, order_id, amount, timestamp)
3. Transforms records to ProcessedRecord format
4. Writes output to S3 processed bucket

Exit codes:
- 0: Success
- 1: Validation/processing error
"""

import argparse
import json
import sys

import boto3
from domain import validate_and_transform
from errors import DomainError

# Required fields for validation
REQUIRED_FIELDS = ["customer_id", "order_id", "amount", "timestamp"]


def main():
    """Main entry point for Flink batch job."""
    parser = argparse.ArgumentParser(description="Flink batch data processor")
    parser.add_argument("--input-bucket", required=True, help="S3 bucket with input data")
    parser.add_argument("--output-bucket", required=True, help="S3 bucket for output data")
    parser.add_argument("--correlation-id", required=True, help="Correlation ID for tracing")
    parser.add_argument("--s3-key", required=True, help="S3 key for input data")
    args = parser.parse_args()

    s3 = boto3.client("s3")

    # Read input data from S3
    print(f"[FLINK] Reading from s3://{args.input_bucket}/{args.s3_key}")
    try:
        response = s3.get_object(Bucket=args.input_bucket, Key=args.s3_key)
        input_data = json.loads(response["Body"].read().decode("utf-8"))

        # Get correlation_id from S3 metadata if available
        metadata = response.get("Metadata", {})
        correlation_id = metadata.get("correlation_id", args.correlation_id)
        audit_key = metadata.get("audit_key", "")

        print(f"[FLINK] Processing correlation_id={correlation_id}")
        if audit_key:
            print(f"[FLINK] Audit trail: s3://{args.input_bucket}/{audit_key}")

    except Exception as e:
        print(f"[FLINK][ERROR] Failed to read input data: {e}")
        sys.exit(1)

    # Extract records from input data
    raw_records = input_data.get("data", [])
    meta = input_data.get("meta", {})
    schema_version = meta.get("schema_version", "unknown")

    print(f"[FLINK] Input schema_version={schema_version}, record_count={len(raw_records)}")

    # Validate and transform
    try:
        processed_records = validate_and_transform(raw_records, REQUIRED_FIELDS)
        print(f"[FLINK] Validation successful: {len(processed_records)} records processed")

    except DomainError as e:
        print(f"[FLINK][ERROR] {e}")
        print(f"[FLINK][ERROR] correlation_id={correlation_id} s3_key={args.s3_key}")
        print(f"[FLINK][ERROR] schema_version={schema_version}")
        if audit_key:
            print(f"[FLINK][ERROR] Check audit trail: s3://{args.input_bucket}/{audit_key}")
        sys.exit(1)

    # Write output to S3
    output_key = f"processed/{correlation_id}/data.json"
    output_data = {
        "records": [r.to_dict() for r in processed_records],
        "meta": {
            "correlation_id": correlation_id,
            "source_key": args.s3_key,
            "record_count": len(processed_records),
            "processor": "flink-batch-job",
        },
    }

    try:
        s3.put_object(
            Bucket=args.output_bucket,
            Key=output_key,
            Body=json.dumps(output_data, indent=2),
            ContentType="application/json",
            Metadata={
                "correlation_id": correlation_id,
                "source_key": args.s3_key,
            },
        )
        print(f"[FLINK] Output written to s3://{args.output_bucket}/{output_key}")

    except Exception as e:
        print(f"[FLINK][ERROR] Failed to write output: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
