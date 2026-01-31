"""
S3 Failed Python Demo Orchestrator.

Runs the pipeline and triggers RCA investigation on failure.
"""

from datetime import UTC, datetime

from langsmith import traceable

from app.main import _run
from tests.test_case_s3_failed_python_on_linux import use_case
from tests.utils.alert_factory import create_alert
from tests.utils.file_logger import configure_file_logging

LOG_FILE = "production.log"


def main() -> int:
    configure_file_logging(LOG_FILE)
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    result = use_case.main(log_file=LOG_FILE)
    pipeline_name = result["pipeline_name"]

    if result["status"] == "success":
        print(f"✓ {pipeline_name} succeeded")
        return 0

    raw_alert = create_alert(
        pipeline_name=pipeline_name,
        run_name=run_id,
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
    )

    print("Running investigation...")

    @traceable(
        name=f"S3 Failed Python Investigation - {raw_alert['alert_id'][:8]}",
        metadata={
            "alert_id": raw_alert["alert_id"],
            "pipeline_name": pipeline_name,
            "run_id": run_id,
            "log_file": LOG_FILE,
        },
    )
    def run_with_alert_id():
        return _run(
            alert_name=f"Pipeline failure: {pipeline_name}",
            pipeline_name=pipeline_name,
            severity="critical",
            raw_alert=raw_alert,
        )

    result = run_with_alert_id()

    print(f"\n✓ Pipeline failed. Logs: {LOG_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
