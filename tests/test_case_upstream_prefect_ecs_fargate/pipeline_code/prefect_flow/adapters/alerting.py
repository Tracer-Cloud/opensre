import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.utils.alert_factory import create_alert
from tests.utils.langgraph_client import fire_alert_to_remote_langgraph_client


def fire_pipeline_alert(
    pipeline_name: str, bucket: str, key: str, correlation_id: str, error: Exception
):
    """Standardized alerting for pipeline failures."""
    import os

    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    alert_payload = create_alert(
        pipeline_name=pipeline_name,
        run_name=run_id,
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        annotations={
            "landing_bucket": bucket,
            "s3_key": key,
            "correlation_id": correlation_id,
            "error": str(error),
            "error_type": type(error).__name__,
            "context_sources": "s3,prefect,ecs",
            "prefect_flow": "upstream_downstream_pipeline",
            "ecs_cluster": "tracer-prefect-cluster",
            "cloudwatch_log_group": "/ecs/tracer-prefect",
            "processed_bucket": os.getenv("PROCESSED_BUCKET", "processed-bucket"),
        },
    )

    try:
        fire_alert_to_remote_langgraph_client(
            alert_name=f"Pipeline failure: {pipeline_name}",
            pipeline_name=pipeline_name,
            severity="critical",
            raw_alert=alert_payload,
        )
    except Exception as fire_error:
        print(f"Failed to fire alert to LangGraph: {fire_error}")
