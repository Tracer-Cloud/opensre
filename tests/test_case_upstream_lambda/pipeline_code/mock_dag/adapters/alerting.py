import json
import logging
from datetime import UTC, datetime

try:
    from tests.utils.alert_factory import create_alert
    from tests.utils.langgraph_client import fire_alert_to_remote_langgraph_client
except ImportError:
    create_alert = None
    fire_alert_to_remote_langgraph_client = None

logger = logging.getLogger(__name__)


def fire_pipeline_alert(
    pipeline_name: str, bucket: str, key: str, correlation_id: str, error: Exception
):
    """Standardized alerting for pipeline failures."""
    logger.error(
        json.dumps(
            {
                "event": "pipeline_alert",
                "pipeline": pipeline_name,
                "bucket": bucket,
                "key": key,
                "correlation_id": correlation_id,
                "error": str(error),
                "error_type": type(error).__name__,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
    )

    if not create_alert or not fire_alert_to_remote_langgraph_client:
        logger.warning("Alert utilities not available (Lambda environment)")
        return

    try:
        run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        alert_payload = create_alert(
            pipeline_name=pipeline_name,
            run_name=run_id,
            status="failed",
            timestamp=datetime.now(UTC).isoformat(),
            annotations={
                "s3_bucket": bucket,
                "s3_key": key,
                "correlation_id": correlation_id,
                "error": str(error),
                "error_type": type(error).__name__,
                "context_sources": "s3,lambda",
            },
        )

        fire_alert_to_remote_langgraph_client(
            alert_name=f"Pipeline failure: {pipeline_name}",
            pipeline_name=pipeline_name,
            severity="critical",
            raw_alert=alert_payload,
        )
        logger.info("Alert sent to LangGraph endpoint successfully")
    except Exception as fire_error:
        logger.warning(f"Failed to fire alert to LangGraph: {fire_error}")
