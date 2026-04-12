"""
Simulated Data Engineering Pipeline - Pure Business Logic.

No alerting or RCA orchestration logic lives here.
"""

import logging
import os
import time
import uuid
from datetime import UTC, datetime

from opentelemetry import trace

from tests.shared.tracer_ingest import emit_tool_event
from tests.utils.command_runner import MAX_LINE, run_tool

logger = logging.getLogger(__name__)

PIPELINE_NAME = "demo_pipeline_s3_failed_python"

_tracer = trace.get_tracer("s3-failed-pipeline")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_step(
    *,
    trace_id: str,
    run_id: str,
    run_name: str,
    tool_id: str,
    tool_name: str,
    tool_cmd: str,
    cmd: list[str],
    timeout: int,
    metadata: dict,
) -> dict:
    start_iso = _utc_now_iso()
    start_monotonic = time.monotonic()
    result = run_tool(cmd, timeout=timeout, step_name=tool_id)
    end_iso = _utc_now_iso()
    runtime_ms = int((time.monotonic() - start_monotonic) * 1000)

    meta = dict(metadata or {})
    meta.update(
        {
            "command": result.get("command"),
            "stderr_summary": result.get("stderr_summary"),
            "stdout_summary": result.get("stdout_summary"),
            "runtime_ms": runtime_ms,
        }
    )

    emit_tool_event(
        trace_id=trace_id,
        run_id=run_id,
        run_name=run_name,
        tool_id=tool_id,
        tool_name=tool_name,
        tool_cmd=tool_cmd,
        start_time=start_iso,
        end_time=end_iso,
        exit_code=result.get("exit_code", 1),
        metadata=meta,
    )

    return result


def step1_check_s3_object(execution_run_id: str, run_id: str, trace_id: str) -> dict:
    with _tracer.start_as_current_span("step1_check_s3_object") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "check_s3_object")

        logger.info("STEP 1: aws s3api head-object")
        time.sleep(3)
        cmd = [
            "aws",
            "s3api",
            "head-object",
            "--bucket",
            "tracer-data-lake-prod",
            "--key",
            "raw/events/2024/01/events.parquet",
        ]
        result = _run_step(
            trace_id=trace_id,
            run_id=run_id,
            run_name=PIPELINE_NAME,
            tool_id="prefect_task_check_s3_object",
            tool_name="S3: Check landing object",
            tool_cmd="aws s3api head-object",
            cmd=cmd,
            timeout=15,
            metadata={
                "s3_bucket": "tracer-data-lake-prod",
                "s3_key": "raw/events/2024/01/events.parquet",
                "correlation_id": execution_run_id,
            },
        )

        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step1_check_s3_object failed exit_code=%s", result["exit_code"])

        return result


def step2_download_from_s3(execution_run_id: str, run_id: str, trace_id: str) -> dict:
    with _tracer.start_as_current_span("step2_download_from_s3") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "download_from_s3")

        logger.info("STEP 2: aws s3 cp")
        time.sleep(3)
        cmd = [
            "aws",
            "s3",
            "cp",
            "s3://tracer-pipeline-artifacts/raw/events/dataset.json",
            "/tmp/dataset.json",
        ]
        result = _run_step(
            trace_id=trace_id,
            run_id=run_id,
            run_name=PIPELINE_NAME,
            tool_id="prefect_task_download_s3",
            tool_name="S3: Download dataset",
            tool_cmd="aws s3 cp",
            cmd=cmd,
            timeout=15,
            metadata={
                "s3_bucket": "tracer-pipeline-artifacts",
                "s3_key": "raw/events/dataset.json",
                "destination": "/tmp/dataset.json",
                "correlation_id": execution_run_id,
            },
        )

        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step2_download_from_s3 failed exit_code=%s", result["exit_code"])

        return result


def step3_list_s3_bucket(execution_run_id: str, run_id: str, trace_id: str) -> dict:
    with _tracer.start_as_current_span("step3_list_s3_bucket") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "list_s3_bucket")

        logger.info("STEP 3: aws s3 ls")
        time.sleep(3)
        cmd = [
            "aws",
            "s3",
            "ls",
            "s3://tracer-etl-staging/raw/events/",
        ]
        result = _run_step(
            trace_id=trace_id,
            run_id=run_id,
            run_name=PIPELINE_NAME,
            tool_id="prefect_task_list_s3",
            tool_name="S3: List staging prefix",
            tool_cmd="aws s3 ls",
            cmd=cmd,
            timeout=15,
            metadata={
                "s3_bucket": "tracer-etl-staging",
                "s3_prefix": "raw/events/",
                "correlation_id": execution_run_id,
            },
        )

        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step3_list_s3_bucket failed exit_code=%s", result["exit_code"])

        return result


def step4_process_json_with_jq(execution_run_id: str, run_id: str, trace_id: str) -> dict:
    with _tracer.start_as_current_span("step4_process_json_with_jq") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "process_json_with_jq")

        logger.info("STEP 4: jq process JSON")
        time.sleep(3)
        cmd = [
            "jq",
            "-r",
            ".events[] | {user_id: .user_id, event: .event_type, ts: .timestamp}",
            "/tmp/tracer_events.json",
        ]
        result = _run_step(
            trace_id=trace_id,
            run_id=run_id,
            run_name=PIPELINE_NAME,
            tool_id="prefect_task_process_json",
            tool_name="Transform events (jq)",
            tool_cmd="jq process json",
            cmd=cmd,
            timeout=10,
            metadata={
                "input_file": "/tmp/tracer_events.json",
                "correlation_id": execution_run_id,
            },
        )

        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step4_process_json_with_jq failed exit_code=%s", result["exit_code"])

        return result


def step5_transform_with_jq(execution_run_id: str, run_id: str, trace_id: str) -> dict:
    with _tracer.start_as_current_span("step5_transform_with_jq") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "transform_with_jq")

        logger.info("STEP 5: jq transform")
        time.sleep(3)
        cmd = [
            "jq",
            "-c",
            'select(.status == "active") | .id',
            "/tmp/tracer_users.json",
        ]
        result = _run_step(
            trace_id=trace_id,
            run_id=run_id,
            run_name=PIPELINE_NAME,
            tool_id="prefect_task_transform_json",
            tool_name="Validate users (jq)",
            tool_cmd="jq transform",
            cmd=cmd,
            timeout=10,
            metadata={
                "input_file": "/tmp/tracer_users.json",
                "correlation_id": execution_run_id,
            },
        )

        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step5_transform_with_jq failed exit_code=%s", result["exit_code"])

        return result


def main(
    log_file: str = "production.log", run_id: str | None = None, trace_id: str | None = None
) -> dict:
    execution_run_id = run_id or str(uuid.uuid4())
    trace_identifier = trace_id or execution_run_id

    logger.info(
        "DATA ENGINEERING PIPELINE START main_pid=%s log_file=%s execution_run_id=%s",
        os.getpid(),
        log_file,
        execution_run_id,
    )
    start_time = time.time()
    results: list[dict] = []

    with _tracer.start_as_current_span("process_pipeline") as root_span:
        root_span.set_attribute("execution.run_id", execution_run_id)
        root_span.set_attribute("pipeline.name", PIPELINE_NAME)

        for step_func in (
            step1_check_s3_object,
            step2_download_from_s3,
            step3_list_s3_bucket,
            step4_process_json_with_jq,
            step5_transform_with_jq,
        ):
            try:
                results.append(step_func(execution_run_id, execution_run_id, trace_identifier))
            except Exception as exc:
                logger.exception("%s exception: %s", step_func.__name__, exc)
                results.append(
                    {
                        "step_name": step_func.__name__,
                        "command": "",
                        "exit_code": 1,
                        "stderr_summary": str(exc)[:MAX_LINE],
                        "stdout_summary": "",
                    }
                )

        elapsed = time.time() - start_time
        failed = [result for result in results if result["exit_code"] != 0]
        status = "failed" if failed else "success"

        root_span.set_attribute("runtime_sec", elapsed)
        root_span.set_attribute("failed_steps", len(failed))
        root_span.set_attribute("total_steps", len(results))
        root_span.set_attribute("status", status)

        logger.info(
            "PIPELINE SUMMARY runtime_sec=%.2f failed=%s total=%s",
            elapsed,
            len(failed),
            len(results),
        )
        for result in results:
            step_name = result["step_name"]
            status_label = "FAILED" if result["exit_code"] != 0 else "SUCCESS"
            logger.info("  %s: %s exit_code=%s", step_name, status_label, result["exit_code"])

    return {
        "pipeline_name": PIPELINE_NAME,
        "status": status,
        "results": results,
        "failed_steps": failed,
        "runtime_sec": elapsed,
        "execution_run_id": execution_run_id,
        "trace_id": trace_identifier,
    }


if __name__ == "__main__":
    raise SystemExit(main())
