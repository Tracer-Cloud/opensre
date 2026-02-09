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

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
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


std_logger = logging.getLogger("prefect_flow")
std_logger.setLevel(logging.INFO)

CONNECTIVITY_TIMEOUT_SECONDS = 5
CONNECTIVITY_SAMPLE_BYTES = 512


def _format_response_sample(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace").strip()
    return " ".join(text.split())


def _parse_otlp_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    raw_headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
    if raw_headers:
        for pair in raw_headers.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()
    if not headers:
        auth_header = os.getenv("GCLOUD_OTLP_AUTH_HEADER")
        if auth_header:
            headers["Authorization"] = auth_header
    return headers


def _check_http_get(
    logger,
    label: str,
    url: str,
    headers: dict[str, str] | None = None,
    log_body: bool = False,
    log_to_stdout: bool = False,
) -> None:
    try:
        request_headers = headers or {}
        req = urllib.request.Request(url, headers=request_headers, method="GET")
        with urllib.request.urlopen(req, timeout=CONNECTIVITY_TIMEOUT_SECONDS) as response:
            body = response.read(CONNECTIVITY_SAMPLE_BYTES) if log_body else b""
            message = f"{label} GET {url} -> {response.status} {response.reason}"
            if log_body:
                message += f" response={_format_response_sample(body)}"
            _log(logger, message)
            if log_to_stdout:
                _log_stdout(message)
    except urllib.error.HTTPError as exc:
        body = exc.read(CONNECTIVITY_SAMPLE_BYTES) if log_body else b""
        message = f"{label} GET {url} -> {exc.code} {exc.reason}"
        if log_body:
            message += f" response={_format_response_sample(body)}"
        _log(logger, message)
        if log_to_stdout:
            _log_stdout(message)
    except urllib.error.URLError as exc:
        message = f"{label} GET {url} failed: {exc.reason}"
        _log(logger, message)
        if log_to_stdout:
            _log_stdout(message)


def _run_connectivity_checks(logger) -> None:
    external_url = "https://example.com/"
    _check_http_get(logger, "External", external_url)

    grafana_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv(
        "GCLOUD_OTLP_ENDPOINT"
    )
    if not grafana_endpoint:
        _log(logger, "Grafana connectivity check skipped: no OTLP endpoint configured")
        return

    _check_http_get(
        logger,
        "Grafana",
        grafana_endpoint,
        headers=_parse_otlp_headers(),
        log_body=True,
        log_to_stdout=True,
    )


def _log(logger, message: str) -> None:
    logger.info(message)
    std_logger.info(message)


def _log_stdout(message: str) -> None:
    print(message)


def _log_json(logger, payload: dict) -> None:
    message = json.dumps(payload)
    logger.info(message)
    std_logger.info(message)


@task(name="extract_data", retries=2, retry_delay_seconds=5)
def extract_data(bucket: str, key: str) -> tuple[dict, str]:
    """
    Read JSON from S3 landing bucket.

    S3 operations are instrumented at the adapter layer with semantic conventions.
    Prefect handles task-level span creation automatically.
    """
    logger = get_run_logger()
    _log(logger, f"Extracting data from s3://{bucket}/{key}")
    raw_payload, correlation_id = read_json(bucket, key)
    record_count = len(raw_payload.get("data", []))
    _log(logger, f"Extracted {record_count} records, correlation_id={correlation_id}")
    return raw_payload, correlation_id


@task(name="transform_data")
def transform_data_task(raw_records: list[dict]) -> list[ProcessedRecord]:
    """
    Validate and transform records using domain logic.

    Domain logic is instrumented at the function level in domain.py.
    Prefect handles task-level span creation automatically.
    """
    logger = get_run_logger()
    _log(logger, f"Validating {len(raw_records)} records")
    processed = validate_and_transform(raw_records, REQUIRED_FIELDS)
    _log(logger, f"Successfully transformed {len(processed)} records")
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
    _log(
        logger,
        f"Loading {len(records)} records to s3://{processed_bucket}/{output_key}",
    )

    output_payload = {"data": [r.to_dict() for r in records]}
    write_json(
        bucket=processed_bucket,
        key=output_key,
        data=output_payload,
        correlation_id=correlation_id,
        source_key=source_key,
    )

    _log(logger, "Data loaded successfully")


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
    logger = get_run_logger()
    _log(logger, f"Starting pipeline for s3://{bucket}/{key}")
    start_time = time.monotonic()

    correlation_id = "unknown"
    execution_run_id = str(flow_run.id) if flow_run.id else None
    raw_record_count = 0

    try:
        _run_connectivity_checks(logger)

        # Extract
        raw_payload, correlation_id = extract_data(bucket, key)
        if execution_run_id is None:
            execution_run_id = correlation_id
        raw_records = raw_payload.get("data", [])
        raw_record_count = len(raw_records)

        # Log structured input for traceability
        _log_json(
            logger,
            {
                "event": "processing_started",
                "input_bucket": bucket,
                "input_key": key,
                "correlation_id": correlation_id,
                "execution_run_id": execution_run_id,
                "record_count": len(raw_records),
            },
        )

        # Transform
        processed_records = transform_data_task(raw_records)

        # Load
        output_key = key.replace("ingested/", "processed/")
        load_data(processed_records, output_key, correlation_id, key, processed_bucket)

        _log(logger, f"Pipeline completed successfully, correlation_id={correlation_id}")
        telemetry.record_run(
            status="success",
            duration_seconds=time.monotonic() - start_time,
            record_count=len(processed_records),
            attributes={"pipeline.name": PIPELINE_NAME},
        )
        telemetry.flush()
        return {"status": "success", "correlation_id": correlation_id}

    except PipelineError as e:
        _log(logger, f"Pipeline failed: {e}")
        fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
        telemetry.record_run(
            status="failure",
            duration_seconds=time.monotonic() - start_time,
            record_count=raw_record_count,
            failure_count=1,
            attributes={"pipeline.name": PIPELINE_NAME},
        )
        telemetry.flush()
        raise

    except Exception as e:
        _log(logger, f"Unexpected error: {e}")
        fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
        telemetry.record_run(
            status="failure",
            duration_seconds=time.monotonic() - start_time,
            record_count=raw_record_count,
            failure_count=1,
            attributes={"pipeline.name": PIPELINE_NAME},
        )
        telemetry.flush()
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
