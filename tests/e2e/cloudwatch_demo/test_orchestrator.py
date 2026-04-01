"""
CloudWatch Demo Orchestrator.

Run with: make cloudwatch-demo
"""

import sys
import traceback
from datetime import UTC, datetime

from tests.e2e.cloudwatch_demo import use_case
from tests.utils.alert_factory import create_alert
from tests.utils.cloudwatch_logger import log_error_to_cloudwatch
from tests.utils.conftest import get_test_config


def main(test_name: str = "demo-pipeline-empty-file-error") -> int:
    config = get_test_config()
    region = config["aws_region"]

    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    try:
        result = use_case.main()
        print(f"✓ {result['pipeline_name']} succeeded: {result['rows_processed']} rows")
        return 0

    except Exception as e:
        error_traceback = traceback.format_exc()
        pipeline_name = use_case._pipeline_context["pipeline_name"]

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
                "context_sources": "cloudwatch",
            },
        )

        from langsmith import traceable

        from app.cli.investigate import run_investigation_cli

        print("Running investigation...")

        @traceable(
            run_type="chain",
            name=f"test_cloudwatch_demo - {raw_alert['alert_id'][:8]}",
            metadata={
                "alert_id": raw_alert["alert_id"],
                "pipeline_name": pipeline_name,
                "run_id": run_id,
                "cloudwatch_log_group": cloudwatch_context["log_group"],
                "log_stream": cloudwatch_context.get("log_stream"),
            },
        )
        def run_with_alert_id():
            return run_investigation_cli(
                alert_name=f"Pipeline failure: {pipeline_name}",
                pipeline_name=pipeline_name,
                severity="critical",
                raw_alert=raw_alert,
            )

        run_with_alert_id()

        print(f"\n✓ CloudWatch logs: {cloudwatch_context['cloudwatch_url']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
