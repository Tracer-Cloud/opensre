"""Airflow DAG for Upstream/Downstream Pipeline (Airflow 3.1.6)."""

import json
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.exceptions import AirflowFailException
from airflow.sdk import task

for parent in Path(__file__).resolve().parents:
    telemetry_root = parent / "shared" / "telemetry"
    if telemetry_root.exists():
        sys.path.insert(0, str(telemetry_root))
        break

from tracer_telemetry import init_telemetry

from airflow_dag.adapters.alerting import log_pipeline_alert
from airflow_dag.adapters.s3 import read_json, write_json
from airflow_dag.config import PIPELINE_NAME, PROCESSED_BUCKET, REQUIRED_FIELDS
from airflow_dag.domain import validate_and_transform
from airflow_dag.errors import DomainError, PipelineError

logger = logging.getLogger("airflow.task")
telemetry = init_telemetry(
    service_name="airflow-etl-pipeline",
    resource_attributes={
        "pipeline.name": PIPELINE_NAME,
        "pipeline.framework": "airflow",
    },
)
tracer = telemetry.tracer


@task
def extract_data(**context) -> dict:
    """Read JSON from S3 landing bucket."""
    dag_run = context.get("dag_run")
    conf = dag_run.conf if dag_run else {}
    bucket = conf.get("bucket")
    key = conf.get("key")

    if not bucket or not key:
        raise AirflowFailException("Missing bucket/key in dag_run.conf")

    with tracer.start_as_current_span("extract_data") as span:
        span.set_attribute("s3.bucket", bucket)
        span.set_attribute("s3.key", key)
        start_time = time.monotonic()

        raw_payload, correlation_id = read_json(bucket, key)
        record_count = len(raw_payload.get("data", []))
        span.set_attribute("record_count", record_count)
        span.set_attribute("correlation_id", correlation_id)

        logger.info(
            json.dumps(
                {
                    "event": "extract_completed",
                    "bucket": bucket,
                    "key": key,
                    "record_count": record_count,
                    "correlation_id": correlation_id,
                }
            )
        )

        return {
            "bucket": bucket,
            "key": key,
            "raw_payload": raw_payload,
            "correlation_id": correlation_id,
            "start_time": start_time,
        }


@task
def transform_data(payload: dict, **context) -> dict:
    """Validate and transform records using domain logic."""
    raw_records = payload.get("raw_payload", {}).get("data", [])
    correlation_id = payload.get("correlation_id", "unknown")

    with tracer.start_as_current_span("transform_data") as span:
        span.set_attribute("record_count", len(raw_records))
        span.set_attribute("correlation_id", correlation_id)
        logger.info(
            json.dumps(
                {
                    "event": "transform_started",
                    "record_count": len(raw_records),
                    "correlation_id": correlation_id,
                }
            )
        )

        try:
            processed = validate_and_transform(raw_records, REQUIRED_FIELDS)
        except DomainError as e:
            dag_run = context.get("dag_run")
            log_pipeline_alert(
                pipeline_name=PIPELINE_NAME,
                bucket=payload.get("bucket", "unknown"),
                key=payload.get("key", "unknown"),
                correlation_id=correlation_id,
                error=e,
                dag_id=dag_run.dag_id if dag_run else None,
                dag_run_id=dag_run.run_id if dag_run else None,
            )
            telemetry.record_run(
                status="failure",
                duration_seconds=None,
                record_count=len(raw_records),
                failure_count=1,
                attributes={"pipeline.name": PIPELINE_NAME},
            )
            raise AirflowFailException(str(e)) from e

        return {
            "bucket": payload.get("bucket"),
            "key": payload.get("key"),
            "correlation_id": correlation_id,
            "processed_records": [record.to_dict() for record in processed],
            "start_time": payload.get("start_time"),
        }


@task
def load_data(payload: dict) -> None:
    """Write processed data to S3."""
    source_key = payload.get("key", "")
    output_key = source_key.replace("ingested/", "processed/")
    correlation_id = payload.get("correlation_id", "unknown")
    records = payload.get("processed_records", [])
    start_time = payload.get("start_time")

    with tracer.start_as_current_span("load_data") as span:
        span.set_attribute("s3.bucket", PROCESSED_BUCKET)
        span.set_attribute("s3.key", output_key)
        span.set_attribute("record_count", len(records))
        span.set_attribute("correlation_id", correlation_id)

        logger.info(
            json.dumps(
                {
                    "event": "load_started",
                    "output_key": output_key,
                    "record_count": len(records),
                    "correlation_id": correlation_id,
                }
            )
        )

        output_payload = {"data": records, "processed_at": datetime.now(UTC).isoformat()}
        try:
            write_json(
                bucket=PROCESSED_BUCKET,
                key=output_key,
                data=output_payload,
                correlation_id=correlation_id,
                source_key=source_key,
            )
        except PipelineError as e:
            telemetry.record_run(
                status="failure",
                duration_seconds=None,
                record_count=len(records),
                failure_count=1,
                attributes={"pipeline.name": PIPELINE_NAME},
            )
            raise AirflowFailException(str(e)) from e

        logger.info(
            json.dumps(
                {
                    "event": "load_completed",
                    "output_key": output_key,
                    "record_count": len(records),
                    "correlation_id": correlation_id,
                }
            )
        )

        duration_seconds = None
        if isinstance(start_time, (int, float)):
            duration_seconds = time.monotonic() - start_time
        telemetry.record_run(
            status="success",
            duration_seconds=duration_seconds,
            record_count=len(records),
            attributes={"pipeline.name": PIPELINE_NAME},
        )


with DAG(
    dag_id="upstream_downstream_pipeline_airflow",
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    schedule=None,
    catchup=False,
    default_args={"retries": 1},
    tags=["tracer", "airflow", "ecs"],
) as dag:
    extracted = extract_data()
    transformed = transform_data(extracted)
    load_data(transformed)

__all__ = ["dag"]
