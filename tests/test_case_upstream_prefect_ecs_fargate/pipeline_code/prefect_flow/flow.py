"""
Prefect Flow for Upstream/Downstream Pipeline.

This is a Prefect 3.x implementation of the data pipeline that:
1. Extracts data from S3 landing bucket
2. Validates and transforms the data
3. Loads processed data to S3 processed bucket

Run locally:
    python -c "from flow import data_pipeline_flow; data_pipeline_flow('bucket', 'key')"
"""

from prefect import flow, get_run_logger, task

from .adapters.alerting import fire_pipeline_alert
from .adapters.s3 import read_json, write_json
from .config import PIPELINE_NAME, PROCESSED_BUCKET, REQUIRED_FIELDS
from .domain import validate_and_transform
from .errors import PipelineError
from .schemas import ProcessedRecord


@task(name="extract_data", retries=2, retry_delay_seconds=5)
def extract_data(bucket: str, key: str) -> tuple[dict, str]:
    """Read JSON from S3 landing bucket."""
    logger = get_run_logger()
    logger.info(f"Extracting data from s3://{bucket}/{key}")

    raw_payload, correlation_id = read_json(bucket, key)
    record_count = len(raw_payload.get("data", []))
    logger.info(f"Extracted {record_count} records, correlation_id={correlation_id}")

    return raw_payload, correlation_id


@task(name="transform_data")
def transform_data(raw_records: list[dict]) -> list[ProcessedRecord]:
    """Validate and transform records using domain logic."""
    logger = get_run_logger()
    logger.info(f"Transforming {len(raw_records)} records")

    processed = validate_and_transform(raw_records, REQUIRED_FIELDS)
    logger.info(f"Successfully transformed {len(processed)} records")

    return processed


@task(name="load_data", retries=2, retry_delay_seconds=5)
def load_data(
    records: list[ProcessedRecord],
    output_key: str,
    correlation_id: str,
    source_key: str,
):
    """Write processed data to S3."""
    logger = get_run_logger()
    logger.info(f"Loading {len(records)} records to s3://{PROCESSED_BUCKET}/{output_key}")

    output_payload = {"data": [r.to_dict() for r in records]}
    write_json(
        bucket=PROCESSED_BUCKET,
        key=output_key,
        data=output_payload,
        correlation_id=correlation_id,
        source_key=source_key,
    )

    logger.info("Data loaded successfully")


@flow(name="upstream_downstream_pipeline")
def data_pipeline_flow(bucket: str, key: str) -> dict:
    """
    Main ETL flow for processing upstream data.

    Args:
        bucket: S3 bucket containing the input data
        key: S3 key for the input file

    Returns:
        dict with status and correlation_id
    """
    import json

    logger = get_run_logger()
    logger.info(f"Starting pipeline for s3://{bucket}/{key}")

    correlation_id = "unknown"

    try:
        # Extract
        raw_payload, correlation_id = extract_data(bucket, key)
        raw_records = raw_payload.get("data", [])

        # Log structured input for traceability
        logger.info(
            json.dumps(
                {
                    "event": "processing_started",
                    "input_bucket": bucket,
                    "input_key": key,
                    "correlation_id": correlation_id,
                    "record_count": len(raw_records),
                }
            )
        )

        # Transform
        processed_records = transform_data(raw_records)

        # Load
        output_key = key.replace("ingested/", "processed/")
        load_data(processed_records, output_key, correlation_id, key)

        logger.info(f"Pipeline completed successfully, correlation_id={correlation_id}")
        return {"status": "success", "correlation_id": correlation_id}

    except PipelineError as e:
        logger.error(f"Pipeline failed: {e}")
        fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
        raise

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
        raise


if __name__ == "__main__":
    # For local testing
    import sys

    if len(sys.argv) == 3:
        bucket, key = sys.argv[1], sys.argv[2]
        result = data_pipeline_flow(bucket, key)
        print(f"Result: {result}")
    else:
        print("Usage: python flow.py <bucket> <key>")
