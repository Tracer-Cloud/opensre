import json
import logging


def log_pipeline_alert(
    pipeline_name: str,
    bucket: str,
    key: str,
    correlation_id: str,
    error: Exception,
    dag_id: str | None,
    dag_run_id: str | None,
) -> None:
    """Log a structured alert event for pipeline failures."""
    logger = logging.getLogger("airflow.task")
    payload = {
        "event": "pipeline_failure",
        "pipeline_name": pipeline_name,
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "bucket": bucket,
        "key": key,
        "correlation_id": correlation_id,
        "error": str(error),
        "error_type": type(error).__name__,
    }
    logger.error(json.dumps(payload))
import json
import logging


def log_pipeline_alert(
    pipeline_name: str,
    bucket: str,
    key: str,
    correlation_id: str,
    error: Exception,
    dag_id: str | None,
    dag_run_id: str | None,
) -> None:
    """Log a structured alert event for pipeline failures."""
    logger = logging.getLogger("airflow.task")
    payload = {
        "event": "pipeline_failure",
        "pipeline_name": pipeline_name,
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "bucket": bucket,
        "key": key,
        "correlation_id": correlation_id,
        "error": str(error),
        "error_type": type(error).__name__,
    }
    logger.error(json.dumps(payload))
