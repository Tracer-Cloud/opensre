"""
Superfluid Demo Orchestrator.

Run with: make demo
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from config.grafana_config import load_env

# Load .env file from project root
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_env(env_path, override=True)

from langsmith import traceable  # noqa: E402

from app.agent.output import reset_tracker  # noqa: E402
from app.main import _run  # noqa: E402
from tests.test_case_superfluid import use_case  # noqa: E402
from tests.utils.alert_factory import from_pipeline_run  # noqa: E402


def main() -> int:
    """Run the Superfluid demo orchestrator."""
    reset_tracker()

    # Check required environment variables
    api_key = os.getenv("ANTHROPIC_API_KEY")
    jwt_token = os.getenv("JWT_TOKEN")

    if not api_key:
        print("Error: Missing required environment variable: ANTHROPIC_API_KEY")
        print(f"\nPlease set this in your .env file at: {env_path}")
        return 1

    if not jwt_token:
        print("Error: Missing required environment variable: JWT_TOKEN")
        print(f"\nPlease set this in your .env file at: {env_path}")
        return 1

    print("Finding a real failed pipeline run...")

    # Call use case to find failed run
    web_run = use_case.main()

    if not web_run.get("found"):
        print("No failed runs found in Tracer Web App")
        print(f"Checked {web_run.get('pipelines_checked', 0)} pipelines")
        require_failed_run = os.getenv("SUPERFLUID_REQUIRE_FAILED_RUN", "0").strip().lower()
        if require_failed_run in {"1", "true", "yes"}:
            return 1
        print("Skipping superfluid test (no failed runs available)")
        return 0

    # Extract pipeline details from use case context
    pipeline_name = use_case._run_context["pipeline_name"]
    run_name = use_case._run_context["run_name"]
    trace_id = use_case._run_context["trace_id"]
    status = use_case._run_context["status"]
    run_url = use_case._run_context["run_url"]

    print(f"Found failed run: {run_name}")
    print(f"  Pipeline: {pipeline_name}")
    print(f"  Status: {status}")
    if trace_id:
        print(f"  Trace ID: {trace_id}")
    if run_url:
        print(f"  Run URL: {run_url}")
    print("")

    # Create alert from tracer run
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    raw_alert = from_pipeline_run(
        pipeline_name=pipeline_name,
        run_name=run_name,
        status=status,
        timestamp=timestamp,
        trace_id=trace_id,
        run_url=run_url,
    )

    print("Starting investigation pipeline...")
    print("")

    # Derive alert metadata from context
    alert_name = f"Pipeline failure: {pipeline_name}"
    severity = "critical"

    # Run investigation via main._run() which handles Slack delivery automatically
    @traceable(
        run_type="chain",
        name=f"test_superfluid - {raw_alert.get('alert_id', 'unknown')[:8]}",
        metadata={
            "alert_id": raw_alert.get("alert_id", "unknown"),
            "pipeline_name": pipeline_name,
            "trace_id": trace_id,
            "run_url": run_url,
            "run_name": run_name,
        },
    )
    def run_investigation():
        return _run(
            alert_name=alert_name,
            pipeline_name=pipeline_name,
            severity=severity,
            raw_alert=raw_alert,
        )

    run_investigation()

    return 0


if __name__ == "__main__":
    sys.exit(main())
