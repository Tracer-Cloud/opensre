"""
Prefect Flow for Upstream/Downstream Pipeline.

This is a Prefect 3.x implementation of the data pipeline that:
1. Extracts data from S3 landing bucket
2. Validates and transforms the data
3. Loads processed data to S3 processed bucket

Telemetry Architecture:
- Prefect's native OpenTelemetry integration handles task-level spans automatically
- Domain logic (domain.py) and adapters (s3.py) are instrumented with semantic conventions
- Flow-level metrics (runs, duration, record counts) use proper OpenTelemetry meters
- Context propagation is handled automatically by OpenTelemetry - no manual threading

Run locally:
    python -c "from flow import data_pipeline_flow; data_pipeline_flow('bucket', 'key', 'processed_bucket')"
"""

import sys
import time
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run

for parent in Path(__file__).resolve().parents:
    telemetry_root = parent / "shared" / "telemetry"
    if telemetry_root.exists():
        sys.path.insert(0, str(telemetry_root))
        break

from tracer_telemetry import init_telemetry

from .adapters.alerting import fire_pipeline_alert
from .adapters.s3 import read_json, write_json
from .config import PIPELINE_NAME, REQUIRED_FIELDS
from .domain import validate_and_transform
from .errors import PipelineError
from .schemas import ProcessedRecord

telemetry = init_telemetry(
    service_name="prefect-etl-pipeline",
    resource_attributes={
        "pipeline.name": PIPELINE_NAME,
        "pipeline.framework": "prefect",
    },
)

@task(name="extract_data", retries=2, retry_delay_seconds=5)
def extract_data(bucket: str, key: str) -> tuple[dict, str]:
    """
    Read JSON from S3 landing bucket.

    S3 operations are instrumented at the adapter layer with semantic conventions.
    Prefect handles task-level span creation automatically.
    """
    logger = get_run_logger()
    logger.info(f"Extracting data from s3://{bucket}/{key}")
    raw_payload, correlation_id = read_json(bucket, key)
    record_count = len(raw_payload.get("data", []))
    logger.info(f"Extracted {record_count} records, correlation_id={correlation_id}")
    return raw_payload, correlation_id


@task(name="transform_data")
def transform_data_task(raw_records: list[dict]) -> list[ProcessedRecord]:
    """
    Validate and transform records using domain logic.

    Domain logic is instrumented at the function level in domain.py.
    Prefect handles task-level span creation automatically.
    """
    logger = get_run_logger()
    logger.info(f"Validating {len(raw_records)} records")
    processed = validate_and_transform(raw_records, REQUIRED_FIELDS)
    logger.info(f"Successfully transformed {len(processed)} records")
    return processed


@task(name="load_data", retries=2, retry_delay_seconds=5)
def load_data(
    records: list[ProcessedRecord],
    output_key: str,
    correlation_id: str,
    source_key: str,
    processed_bucket: str,
):
    """
    Write processed data to S3.

    S3 operations are instrumented at the adapter layer with semantic conventions.
    Prefect handles task-level span creation automatically.
    """
    logger = get_run_logger()
    logger.info(
        f"Loading {len(records)} records to s3://{processed_bucket}/{output_key}"
    )

    output_payload = {"data": [r.to_dict() for r in records]}
    write_json(
        bucket=processed_bucket,
        key=output_key,
        data=output_payload,
        correlation_id=correlation_id,
        source_key=source_key,
    )

    logger.info("Data loaded successfully")


@flow(name="upstream_downstream_pipeline")
def data_pipeline_flow(bucket: str, key: str, processed_bucket: str) -> dict:
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
    start_time = time.monotonic()

    correlation_id = "unknown"
    execution_run_id = str(flow_run.id) if flow_run.id else None
    raw_record_count = 0

    try:
        # Extract
        raw_payload, correlation_id = extract_data(bucket, key)
        if execution_run_id is None:
            execution_run_id = correlation_id
        raw_records = raw_payload.get("data", [])
        raw_record_count = len(raw_records)

        # Log structured input for traceability
        logger.info(
            json.dumps(
                {
                    "event": "processing_started",
                    "input_bucket": bucket,
                    "input_key": key,
                    "correlation_id": correlation_id,
                    "execution_run_id": execution_run_id,
                    "record_count": len(raw_records),
                }
            )
        )

        # Transform
        processed_records = transform_data_task(raw_records)

        # Load
        output_key = key.replace("ingested/", "processed/")
        load_data(processed_records, output_key, correlation_id, key, processed_bucket)

        logger.info(f"Pipeline completed successfully, correlation_id={correlation_id}")
        telemetry.record_run(
            status="success",
            duration_seconds=time.monotonic() - start_time,
            record_count=len(processed_records),
            attributes={"pipeline.name": PIPELINE_NAME},
        )
        return {"status": "success", "correlation_id": correlation_id}

    except PipelineError as e:
        logger.error(f"Pipeline failed: {e}")
        fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
        telemetry.record_run(
            status="failure",
            duration_seconds=time.monotonic() - start_time,
            record_count=raw_record_count,
            failure_count=1,
            attributes={"pipeline.name": PIPELINE_NAME},
        )
        raise

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
        telemetry.record_run(
            status="failure",
            duration_seconds=time.monotonic() - start_time,
            record_count=raw_record_count,
            failure_count=1,
            attributes={"pipeline.name": PIPELINE_NAME},
        )
        raise


if __name__ == "__main__":
    # For local testing
    import sys

    if len(sys.argv) == 4:
        bucket, key, processed_bucket = sys.argv[1], sys.argv[2], sys.argv[3]
        result = data_pipeline_flow(bucket, key, processed_bucket)
        print(f"Result: {result}")
    else:
        print("Usage: python flow.py <bucket> <key> <processed_bucket>")
