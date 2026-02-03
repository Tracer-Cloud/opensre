"""
Simulated Data Engineering Pipeline - Pure Business Logic.

No alerting or RCA orchestration logic lives here.
"""

import logging
import os
import sys
import time
import uuid
from pathlib import Path

from tests.utils.command_runner import MAX_LINE, run_tool

# Add shared telemetry to path
_test_root = Path(__file__).parent.parent
sys.path.insert(0, str(_test_root / "shared" / "telemetry"))

from tracer_telemetry import init_telemetry

logger = logging.getLogger(__name__)

PIPELINE_NAME = "demo_pipeline_s3_failed_python"

# Initialize telemetry
_telemetry = None
_tracer = None


def step1_check_s3_object(execution_run_id: str) -> dict:
    with _tracer.start_as_current_span("step1_check_s3_object") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "check_s3_object")
        
        logger.info("STEP 1: aws s3api head-object")
        time.sleep(3)
        result = run_tool(
            [
                "aws",
                "s3api",
                "head-object",
                "--bucket",
                "tracer-data-lake-prod",
                "--key",
                "raw/events/2024/01/events.parquet",
            ],
            timeout=15,
            step_name="step1_check_s3_object",
        )
        
        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step1_check_s3_object failed exit_code=%s", result["exit_code"])
        
        return result


def step2_download_from_s3(execution_run_id: str) -> dict:
    with _tracer.start_as_current_span("step2_download_from_s3") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "download_from_s3")
        
        logger.info("STEP 2: aws s3 cp")
        time.sleep(3)
        result = run_tool(
            [
                "aws",
                "s3",
                "cp",
                "s3://tracer-pipeline-artifacts/raw/events/dataset.json",
                "/tmp/dataset.json",
            ],
            timeout=15,
            step_name="step2_download_from_s3",
        )
        
        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step2_download_from_s3 failed exit_code=%s", result["exit_code"])
        
        return result


def step3_list_s3_bucket(execution_run_id: str) -> dict:
    with _tracer.start_as_current_span("step3_list_s3_bucket") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "list_s3_bucket")
        
        logger.info("STEP 3: aws s3 ls")
        time.sleep(3)
        result = run_tool(
            [
                "aws",
                "s3",
                "ls",
                "s3://tracer-etl-staging/raw/events/",
            ],
            timeout=15,
            step_name="step3_list_s3_bucket",
        )
        
        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step3_list_s3_bucket failed exit_code=%s", result["exit_code"])
        
        return result


def step4_process_json_with_jq(execution_run_id: str) -> dict:
    with _tracer.start_as_current_span("step4_process_json_with_jq") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "process_json_with_jq")
        
        logger.info("STEP 4: jq process JSON")
        time.sleep(3)
        result = run_tool(
            [
                "jq",
                "-r",
                ".events[] | {user_id: .user_id, event: .event_type, ts: .timestamp}",
                "/tmp/tracer_events.json",
            ],
            timeout=10,
            step_name="step4_process_json_with_jq",
        )
        
        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step4_process_json_with_jq failed exit_code=%s", result["exit_code"])
        
        return result


def step5_transform_with_jq(execution_run_id: str) -> dict:
    with _tracer.start_as_current_span("step5_transform_with_jq") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("step_name", "transform_with_jq")
        
        logger.info("STEP 5: jq transform")
        time.sleep(3)
        result = run_tool(
            [
                "jq",
                "-c",
                'select(.status == "active") | .id',
                "/tmp/tracer_users.json",
            ],
            timeout=10,
            step_name="step5_transform_with_jq",
        )
        
        span.set_attribute("exit_code", result["exit_code"])
        if result["exit_code"] != 0:
            span.set_attribute("error", True)
            logger.error("step5_transform_with_jq failed exit_code=%s", result["exit_code"])
        
        return result


def main(log_file: str = "production.log") -> dict:
    global _telemetry, _tracer
    
    # Initialize telemetry
    _telemetry = init_telemetry(
        service_name="s3-failed-pipeline",
        resource_attributes={
            "pipeline.name": PIPELINE_NAME,
            "pipeline.type": "batch",
        },
    )
    _tracer = _telemetry.tracer
    
    execution_run_id = str(uuid.uuid4())
    
    logger.info("DATA ENGINEERING PIPELINE START main_pid=%s log_file=%s execution_run_id=%s", 
                os.getpid(), log_file, execution_run_id)
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
                results.append(step_func(execution_run_id))
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

    # Flush telemetry for short-lived process
    _telemetry.flush()

    return {
        "pipeline_name": PIPELINE_NAME,
        "status": status,
        "results": results,
        "failed_steps": failed,
        "runtime_sec": elapsed,
        "execution_run_id": execution_run_id,
    }


if __name__ == "__main__":
    raise SystemExit(main())
