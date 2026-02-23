import json
import logging
import time

from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run

from ..adapters.alerting import fire_pipeline_alert
from ..adapters.s3 import read_json, write_json
from ..config import PIPELINE_NAME, REQUIRED_FIELDS
from ..domain import validate_and_transform
from ..errors import PipelineError
from ..schemas import ProcessedRecord
from .ancillary import run_connectivity_checks, run_local_flow


class PipelineLogger:
    def __init__(self, prefect_logger) -> None:
        self.prefect = prefect_logger
        self.std = logging.getLogger("prefect_flow")
        self.std.setLevel(logging.INFO)

    def info(self, message: str) -> None:
        self.prefect.info(message)
        self.std.info(message)

    def info_json(self, payload: dict) -> None:
        message = json.dumps(payload)
        self.info(message)


@task(name="extract_data", retries=2, retry_delay_seconds=5)
def extract_data_from_s3(bucket: str, key: str) -> tuple[dict, str]:
    """
    Read JSON from S3 landing bucket.

    S3 operations are instrumented at the adapter layer with semantic conventions.
    Prefect handles task-level span creation automatically.
    """
    logger = PipelineLogger(get_run_logger())
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
    logger = PipelineLogger(get_run_logger())
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
    logger = PipelineLogger(get_run_logger())
    logger.info(f"Loading {len(records)} records to s3://{processed_bucket}/{output_key}")

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
    logger = PipelineLogger(get_run_logger())
    logger.info(f"Starting pipeline for s3://{bucket}/{key}")

    correlation_id = "unknown"
    execution_run_id = str(flow_run.id) if flow_run.id else None

    try:
        run_connectivity_checks(logger, print)

        # Extract
        raw_payload, correlation_id = extract_data_from_s3(bucket, key)
        if execution_run_id is None:
            execution_run_id = correlation_id
        raw_records = raw_payload.get("data", [])

        # Log structured input for traceability
        logger.info_json(
            {
                "event": "processing_started",
                "input_bucket": bucket,
                "input_key": key,
                "correlation_id": correlation_id,
                "execution_run_id": execution_run_id,
                "record_count": len(raw_records),
            }
        )

        # Transform
        processed_records = transform_data_task(raw_records)

        # Load
        output_key = key.replace("ingested/", "processed/")
        load_data(processed_records, output_key, correlation_id, key, processed_bucket)

        logger.info(f"Pipeline completed successfully, correlation_id={correlation_id}")
        return {"status": "success", "correlation_id": correlation_id}

    except PipelineError as e:
        logger.info(f"Pipeline failed: {e}")
        fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
        raise

    except Exception as e:
        logger.info(f"Unexpected error: {e}")
        fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
        raise


if __name__ == "__main__":
    # For local testing
    run_local_flow(data_pipeline_flow)
