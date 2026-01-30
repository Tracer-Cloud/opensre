"""
MWAA Demo Orchestrator - Upstream/Downstream Failure Detection.

Run with: make mwaa-demo

This test case demonstrates the agent's ability to:
1. Detect Airflow task failures
2. Trace failures to upstream data issues (S3)
3. Identify schema changes in upstream data
4. Connect the dots between Lambda ingestion and downstream failures

Milestones:
- M1: Basic MWAA task failure detection
- M2: S3 data boundary tracing
- M3: Lambda upstream producer identification
- M4: External API root cause (full trace)
"""

import os
import sys
from datetime import UTC, datetime

from tests.test_case_mwaa import use_case
from tests.test_case_mwaa.validation import run_validation_suite
from tests.utils.alert_factory import create_alert


def main(inject_failure: bool = True) -> int:
    """
    Run the MWAA upstream/downstream failure test case.

    Args:
        inject_failure: If True, inject schema change to cause failure

    Returns:
        Exit code (0 for success)
    """
    environment_name = os.getenv("MWAA_ENVIRONMENT", "tracer-test-env")
    data_bucket = os.getenv("DATA_BUCKET", "tracer-test-data")

    print("=" * 60)
    print("MWAA Upstream/Downstream Failure Test Case")
    print("=" * 60)
    print(f"Environment: {environment_name}")
    print(f"Inject Failure: {inject_failure}")
    print("")

    # Run the use case
    result = use_case.main(inject_schema_change=inject_failure)

    if result["status"] == "success":
        print(f"✓ Pipeline {result['pipeline_name']} succeeded")
        print("  No failure to investigate.")
        return 0

    # Pipeline failed - create alert and trigger investigation
    print(f"✗ Pipeline {result['pipeline_name']} failed at stage: {result.get('stage', 'unknown')}")

    dag_id = result["pipeline_name"]
    s3_key = result.get("s3_key", "")
    dag_run = result.get("dag_run", {})

    execution_date = dag_run.get("execution_date", datetime.now(UTC).isoformat())
    failed_task = "validate_schema"  # Known failing task for schema issues

    # Create alert with MWAA-specific annotations
    raw_alert = create_alert(
        pipeline_name=dag_id,
        run_name=dag_run.get("run_id", f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"),
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        annotations={
            # MWAA context
            "mwaa_environment": environment_name,
            "dag_id": dag_id,
            "task_id": failed_task,
            "execution_date": execution_date,
            # CloudWatch log context (MWAA logs go to CloudWatch)
            "cloudwatch_log_group": f"airflow-{environment_name}-Task",
            "cloudwatch_log_stream": f"{dag_id}/{failed_task}/{execution_date}",
            "cloudwatch_region": os.getenv("AWS_REGION", "us-east-1"),
            # S3 data context
            "s3_data_bucket": data_bucket,
            "s3_object_key": s3_key,
            # Lambda context
            "lambda_function": os.getenv("API_INGESTER_FUNCTION", "tracer-test-api-ingester"),
            # Failure context
            "error": "Schema validation failed: Missing required fields",
            "schema_change_injected": str(result.get("schema_change_injected", False)),
            "context_sources": "mwaa,cloudwatch,s3,lambda",
        },
    )

    print("\nAlert created with annotations:")
    for key, value in raw_alert.get("annotations", {}).items():
        print(f"  {key}: {value}")

    # Import and run investigation
    from langsmith import traceable

    from app.main import _run

    print("\n" + "=" * 60)
    print("Running Investigation...")
    print("=" * 60)

    @traceable(
        name=f"MWAA Investigation - {raw_alert['alert_id'][:8]}",
        metadata={
            "alert_id": raw_alert["alert_id"],
            "pipeline_name": dag_id,
            "mwaa_environment": environment_name,
            "failure_type": "upstream_schema_change",
        }
    )
    def run_investigation():
        return _run(
            alert_name=f"MWAA Task Failure: {dag_id}/{failed_task}",
            pipeline_name=dag_id,
            severity="critical",
            raw_alert=raw_alert,
        )

    investigation_result = run_investigation()

    # Print summary
    print("\n" + "=" * 60)
    print("Investigation Complete")
    print("=" * 60)

    root_cause = investigation_result.get("root_cause", "Unknown")
    confidence = investigation_result.get("confidence", 0)

    print(f"Root Cause: {root_cause}")
    print(f"Confidence: {confidence:.0%}")

    # Run validation suite
    print("\n" + "=" * 60)
    print("Validation Checkpoints")
    print("=" * 60)

    validation_result = run_validation_suite(investigation_result)

    for checkpoint in validation_result["all_checkpoints"]:
        status = "✓" if checkpoint["passed"] else "✗"
        print(f"  {status} {checkpoint['checkpoint']}: {checkpoint['message']}")

    print(f"\nOverall: {validation_result['summary']['passed']}/{validation_result['summary']['total_checkpoints']} checkpoints passed")

    if validation_result["suite_passed"]:
        print("\n✓ Full trace validation PASSED")
    else:
        print("\n✗ Full trace validation FAILED")
        print("  The agent did not collect evidence from all expected sources.")

    return 0 if validation_result["suite_passed"] else 1


if __name__ == "__main__":
    # Parse command line args
    inject_failure = "--no-failure" not in sys.argv

    sys.exit(main(inject_failure=inject_failure))
