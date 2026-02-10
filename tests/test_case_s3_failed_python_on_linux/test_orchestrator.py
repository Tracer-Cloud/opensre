"""
S3 Failed Python Demo Orchestrator.

Runs the pipeline and triggers RCA investigation on failure.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from langsmith import traceable

from app.main import _run
from tests.test_case_s3_failed_python_on_linux import use_case
from tests.utils.alert_factory import create_alert
from tests.utils.file_logger import configure_file_logging
from tests.shared.tracer_ingest import StepTimer, emit_tool_event

LOG_FILE = "production.log"


def _get_run_and_trace_ids() -> tuple[str, str]:
    """
    Prefer canonical run_id from tracer CLI (exported as TRACER_RUN_ID).

    In CI / any environment where Tracer CLI is used, TRACER_RUN_ID must exist.
    For local dev without tracer init, we allow a fallback timestamp run id.
    """
    tracer_run_id = (os.getenv("TRACER_RUN_ID") or "").strip()
    tracer_trace_id = (os.getenv("TRACER_TRACE_ID") or "").strip()

    if tracer_run_id:
        run_id = tracer_run_id
        # If trace id isn't provided, make a stable one derived from run id
        trace_id = tracer_trace_id or f"trace_{run_id}"
        return run_id, trace_id

    # Local fallback (no tracer init)
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    trace_id = tracer_trace_id or f"trace_{run_id}"
    return run_id, trace_id


def main() -> int:
    configure_file_logging(LOG_FILE)

    run_id, trace_id = _get_run_and_trace_ids()

    # If we are in CI, fail hard if TRACER_RUN_ID is missing to avoid polluting data
    is_ci = (os.getenv("CI") or "").strip().lower() in {"1", "true", "yes"}
    if is_ci and not (os.getenv("TRACER_RUN_ID") or "").strip():
        raise RuntimeError(
            "CI is true but TRACER_RUN_ID is missing. "
            "Make sure 'tracer init' ran and the workflow exported TRACER_RUN_ID."
        )

    # Pipeline start
    emit_tool_event(
        trace_id=trace_id,
        run_id=run_id,
        run_name="s3_failed_python_on_linux",
        tool_id="pipeline_start",
        tool_name="pipeline",
        tool_cmd="start",
        exit_code=0,
        metadata={"log_file": LOG_FILE},
    )

    # Measure use_case execution as a single step
    use_case_step = StepTimer(
        trace_id=trace_id,
        run_id=run_id,
        run_name="s3_failed_python_on_linux",
        tool_id="use_case_main",
        tool_name="python",
        tool_cmd="use_case.main",
    )

    result = use_case.main(log_file=LOG_FILE)
    pipeline_name = result["pipeline_name"]
    status = result.get("status", "unknown")

    use_case_ok = status == "success"
    use_case_step.finish(
        exit_code=0 if use_case_ok else 1,
        metadata={
            "pipeline_name": pipeline_name,
            "status": status,
            "log_file": LOG_FILE,
        },
    )

    if use_case_ok:
        # Pipeline end (success)
        emit_tool_event(
            trace_id=trace_id,
            run_id=run_id,
            run_name=pipeline_name,
            tool_id="pipeline_end",
            tool_name="pipeline",
            tool_cmd="end",
            exit_code=0,
            metadata={"final_status": "success"},
        )
        print(f"✓ {pipeline_name} succeeded")
        return 0

    # Pipeline end (failed)
    emit_tool_event(
        trace_id=trace_id,
        run_id=run_id,
        run_name=pipeline_name,
        tool_id="pipeline_end",
        tool_name="pipeline",
        tool_cmd="end",
        exit_code=1,
        metadata={
            "final_status": "failed",
            "failed_step": "use_case_main",
            "log_file": LOG_FILE,
        },
    )

    raw_alert = create_alert(
        pipeline_name=pipeline_name,
        run_name=run_id,  # IMPORTANT: must match canonical tracer run id
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
    )

    print("Running investigation...")

    # Investigation start
    emit_tool_event(
        trace_id=trace_id,
        run_id=run_id,
        run_name=pipeline_name,
        tool_id="investigation_start",
        tool_name="tracer_agent",
        tool_cmd="run_investigation",
        exit_code=0,
        metadata={"alert_id": raw_alert["alert_id"], "pipeline_name": pipeline_name},
    )

    @traceable(
        run_type="chain",
        name=f"test_s3_failed_python - {raw_alert['alert_id'][:8]}",
        metadata={
            "alert_id": raw_alert["alert_id"],
            "pipeline_name": pipeline_name,
            "run_id": run_id,
            "log_file": LOG_FILE,
            "s3_bucket": raw_alert.get("annotations", {}).get("s3_bucket"),
        },
    )
    def run_with_alert_id():
        return _run(
            alert_name=f"Pipeline failure: {pipeline_name}",
            pipeline_name=pipeline_name,
            severity="critical",
            raw_alert=raw_alert,
        )

    investigation_step = StepTimer(
        trace_id=trace_id,
        run_id=run_id,
        run_name=pipeline_name,
        tool_id="investigation",
        tool_name="tracer_agent",
        tool_cmd="_run",
    )

    investigation_result = run_with_alert_id()

    investigation_step.finish(
        exit_code=0,
        metadata={
            "alert_id": raw_alert["alert_id"],
            "result_type": type(investigation_result).__name__,
        },
    )

    # Investigation end
    emit_tool_event(
        trace_id=trace_id,
        run_id=run_id,
        run_name=pipeline_name,
        tool_id="investigation_end",
        tool_name="tracer_agent",
        tool_cmd="finish_investigation",
        exit_code=0,
        metadata={"alert_id": raw_alert["alert_id"], "status": "completed"},
    )

    print(f"\n✓ Pipeline failed. Logs: {LOG_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
