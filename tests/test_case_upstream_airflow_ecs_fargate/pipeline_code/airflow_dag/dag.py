"""Airflow DAG for Upstream/Downstream Pipeline (Airflow 3.1.6)."""

import json
import logging
from datetime import UTC, datetime

from airflow import DAG
from airflow.decorators import task
from airflow.exceptions import AirflowFailException
from airflow.utils.context import get_current_context
from airflow.utils.dates import days_ago

from airflow_dag.adapters.alerting import log_pipeline_alert
from airflow_dag.adapters.s3 import read_json, write_json
from airflow_dag.config import PIPELINE_NAME, PROCESSED_BUCKET, REQUIRED_FIELDS
from airflow_dag.domain import validate_and_transform
from airflow_dag.errors import DomainError, PipelineError

logger = logging.getLogger("airflow.task")


@task
def extract_data() -> dict:
    """Read JSON from S3 landing bucket."""
    context = get_current_context()
    conf = (context.get("dag_run") and context["dag_run"].conf) or {}
    bucket = conf.get("bucket")
    key = conf.get("key")

    if not bucket or not key:
        raise AirflowFailException("Missing bucket/key in dag_run.conf")

    raw_payload, correlation_id = read_json(bucket, key)
    record_count = len(raw_payload.get("data", []))

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
    }


@task
def transform_data(payload: dict) -> dict:
    """Validate and transform records using domain logic."""
    raw_records = payload.get("raw_payload", {}).get("data", [])
    correlation_id = payload.get("correlation_id", "unknown")

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
        context = get_current_context()
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
        raise AirflowFailException(str(e)) from e

    return {
        "bucket": payload.get("bucket"),
        "key": payload.get("key"),
        "correlation_id": correlation_id,
        "processed_records": [record.to_dict() for record in processed],
    }


@task
def load_data(payload: dict) -> None:
    """Write processed data to S3."""
    source_key = payload.get("key", "")
    output_key = source_key.replace("ingested/", "processed/")
    correlation_id = payload.get("correlation_id", "unknown")
    records = payload.get("processed_records", [])

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


with DAG(
    dag_id="upstream_downstream_pipeline_airflow",
    start_date=days_ago(1),
    schedule=None,
    catchup=False,
    default_args={"retries": 1},
    tags=["tracer", "airflow", "ecs"],
) as dag:
    extracted = extract_data()
    transformed = transform_data(extracted)
    load_data(transformed)

__all__ = ["dag"]
"""Airflow DAG for Upstream/Downstream Pipeline (Airflow 3.1.6)."""

import json
import logging
from datetime import UTC, datetime

from airflow import DAG
from airflow.decorators import task
from airflow.exceptions import AirflowFailException
from airflow.utils.context import get_current_context
from airflow.utils.dates import days_ago

from airflow_dag.adapters.alerting import log_pipeline_alert
from airflow_dag.adapters.s3 import read_json, write_json
from airflow_dag.config import PIPELINE_NAME, PROCESSED_BUCKET, REQUIRED_FIELDS
from airflow_dag.domain import validate_and_transform
from airflow_dag.errors import DomainError, PipelineError

logger = logging.getLogger("airflow.task")


@task
def extract_data() -> dict:
    """Read JSON from S3 landing bucket."""
    context = get_current_context()
    conf = (context.get("dag_run") and context["dag_run"].conf) or {}
    bucket = conf.get("bucket")
    key = conf.get("key")

    if not bucket or not key:
        raise AirflowFailException("Missing bucket/key in dag_run.conf")

    raw_payload, correlation_id = read_json(bucket, key)
    record_count = len(raw_payload.get("data", []))

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
    }


@task
def transform_data(payload: dict) -> dict:
    """Validate and transform records using domain logic."""
    raw_records = payload.get("raw_payload", {}).get("data", [])
    correlation_id = payload.get("correlation_id", "unknown")

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
        context = get_current_context()
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
        raise AirflowFailException(str(e)) from e

    return {
        "bucket": payload.get("bucket"),
        "key": payload.get("key"),
        "correlation_id": correlation_id,
        "processed_records": [record.to_dict() for record in processed],
    }


@task
def load_data(payload: dict) -> None:
    """Write processed data to S3."""
    source_key = payload.get("key", "")
    output_key = source_key.replace("ingested/", "processed/")
    correlation_id = payload.get("correlation_id", "unknown")
    records = payload.get("processed_records", [])

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


with DAG(
    dag_id="upstream_downstream_pipeline_airflow",
    start_date=days_ago(1),
    schedule=None,
    catchup=False,
    default_args={"retries": 1},
    tags=["tracer", "airflow", "ecs"],
) as dag:
    extracted = extract_data()
    transformed = transform_data(extracted)
    load_data(transformed)

__all__ = ["dag"]
