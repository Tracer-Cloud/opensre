"""
CloudWatch Demo Orchestrator.

Run with: make cloudwatch-demo
"""

import sys
import traceback
from datetime import UTC, datetime

import requests

from tests.cloudwatch_demo import customer_pipeline
from tests.conftest import get_test_config
from tests.utils.alert_factory import create_alert
from tests.utils.cloudwatch_logger import log_error_to_cloudwatch
from tests.utils.langgraph_client import (
    fire_alert_to_langgraph,
    stream_investigation_results,
)

def main(test_name: str = "demo-pipeline-empty-file-error") -> int:
    config = get_test_config()
    region = config["aws_region"]

    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    try:
        result = customer_pipeline.main()
        print(f"✓ {result['pipeline_name']} succeeded: {result['rows_processed']} rows")
        return 0

    except Exception as e:
        error_traceback = traceback.format_exc()
        pipeline_name = customer_pipeline._pipeline_context["pipeline_name"]

        cloudwatch_context = log_error_to_cloudwatch(
            error=e,
            error_traceback=error_traceback,
            pipeline_name=pipeline_name,
            run_id=run_id,
            test_name=test_name,
            region=region,
        )
        print(f"✓ Logged to CloudWatch: {cloudwatch_context['log_group']}")
        print(f"  {cloudwatch_context['cloudwatch_url']}\n")

        raw_alert = create_alert(
            pipeline_name=pipeline_name,
            run_name=run_id,
            status="failed",
            timestamp=datetime.now(UTC).isoformat(),
            annotations={
                "cloudwatch_log_group": cloudwatch_context["log_group"],
                "cloudwatch_log_stream": cloudwatch_context["log_stream"],
                "cloudwatch_logs_url": cloudwatch_context["cloudwatch_url"],
                "cloudwatch_region": region,
                "error": cloudwatch_context["error_message"],
            },
        )

        from app.agent.graph_pipeline import run_investigation

        print("Running investigation...")
        run_investigation(
            alert_name=f"Pipeline failure: {pipeline_name}",
            pipeline_name=pipeline_name,
            severity="critical",
            raw_alert=raw_alert,
        )

        print(f"\n✓ CloudWatch logs: {cloudwatch_context['cloudwatch_url']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
