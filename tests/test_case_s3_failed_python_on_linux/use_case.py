"""
Simulated Data Engineering Pipeline - Pure Business Logic.

No alerting or RCA orchestration logic lives here.
"""

import logging
import os
import time

from tests.utils.command_runner import MAX_LINE, run_tool

logger = logging.getLogger(__name__)

PIPELINE_NAME = "demo_pipeline_s3_failed_python"


def step1_check_s3_object() -> dict:
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
    if result["exit_code"] != 0:
        logger.error("step1_check_s3_object failed exit_code=%s", result["exit_code"])
    return result


def step2_download_from_s3() -> dict:
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
    if result["exit_code"] != 0:
        logger.error("step2_download_from_s3 failed exit_code=%s", result["exit_code"])
    return result


def step3_list_s3_bucket() -> dict:
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
    if result["exit_code"] != 0:
        logger.error("step3_list_s3_bucket failed exit_code=%s", result["exit_code"])
    return result


def step4_process_json_with_jq() -> dict:
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
    if result["exit_code"] != 0:
        logger.error("step4_process_json_with_jq failed exit_code=%s", result["exit_code"])
    return result


def step5_transform_with_jq() -> dict:
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
    if result["exit_code"] != 0:
        logger.error("step5_transform_with_jq failed exit_code=%s", result["exit_code"])
    return result


def main(log_file: str = "production.log") -> dict:
    logger.info("DATA ENGINEERING PIPELINE START main_pid=%s log_file=%s", os.getpid(), log_file)
    start_time = time.time()
    results: list[dict] = []

    for step_func in (
        step1_check_s3_object,
        step2_download_from_s3,
        step3_list_s3_bucket,
        step4_process_json_with_jq,
        step5_transform_with_jq,
    ):
        try:
            results.append(step_func())
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
    }


if __name__ == "__main__":
    raise SystemExit(main())
