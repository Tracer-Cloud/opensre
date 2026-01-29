#!/usr/bin/env python3
"""
Data Engineering Pipeline - Multiple steps that fail with exit codes.
Logs to production.log with rich output for failure diagnosis.
"""

import logging
import os
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("production.log", mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

MAX_LINE = 20000


def run_tool(cmd, timeout=10, step_name=""):
    """Run CLI tool and return exit code. Logs full stdout/stderr for diagnosis."""
    cmd_str = " ".join(cmd)
    logger.info("command=%s step=%s parent_pid=%s", cmd_str, step_name, os.getpid())

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    logger.info("tool_pid=%s", process.pid)

    try:
        stdout, stderr = process.communicate(timeout=timeout)
        exit_code = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        exit_code = process.returncode
        logger.error(
            "step=%s timeout=%s exit_code=%s",
            step_name,
            timeout,
            exit_code,
        )

    out_decoded = stdout.decode("utf-8", errors="replace") if stdout else ""
    err_decoded = stderr.decode("utf-8", errors="replace") if stderr else ""

    if out_decoded.strip():
        logger.info("step=%s exit_code=%s stdout_len=%s", step_name, exit_code, len(out_decoded))
        for line in out_decoded.strip().splitlines():
            logger.info("stdout: %s", line[:MAX_LINE] if len(line) > MAX_LINE else line)
    if err_decoded.strip():
        logger.error("step=%s exit_code=%s stderr_len=%s", step_name, exit_code, len(err_decoded))
        for line in err_decoded.strip().splitlines():
            logger.error("stderr: %s", line[:MAX_LINE] if len(line) > MAX_LINE else line)

    logger.info("step=%s exit_code=%s", step_name, exit_code)
    return exit_code


def step1_check_s3_object():
    logger.info("STEP 1: aws s3api head-object")
    time.sleep(3)
    exit_code = run_tool(
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
    if exit_code != 0:
        logger.error("step1_check_s3_object failed exit_code=%s", exit_code)
    return exit_code


def step2_download_from_s3():
    logger.info("STEP 2: aws s3 cp")
    time.sleep(3)
    exit_code = run_tool(
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
    if exit_code != 0:
        logger.error("step2_download_from_s3 failed exit_code=%s", exit_code)
    return exit_code


def step3_list_s3_bucket():
    logger.info("STEP 3: aws s3 ls")
    time.sleep(3)
    exit_code = run_tool(
        [
            "aws",
            "s3",
            "ls",
            "s3://tracer-etl-staging/raw/events/",
        ],
        timeout=15,
        step_name="step3_list_s3_bucket",
    )
    if exit_code != 0:
        logger.error("step3_list_s3_bucket failed exit_code=%s", exit_code)
    return exit_code


def step4_process_json_with_jq():
    logger.info("STEP 4: jq process JSON")
    time.sleep(3)
    exit_code = run_tool(
        [
            "jq",
            "-r",
            ".events[] | {user_id: .user_id, event: .event_type, ts: .timestamp}",
            "/tmp/tracer_events.json",
        ],
        timeout=10,
        step_name="step4_process_json_with_jq",
    )
    if exit_code != 0:
        logger.error("step4_process_json_with_jq failed exit_code=%s", exit_code)
    return exit_code


def step5_transform_with_jq():
    logger.info("STEP 5: jq transform")
    time.sleep(3)
    exit_code = run_tool(
        [
            "jq",
            "-c",
            'select(.status == "active") | .id',
            "/tmp/tracer_users.json",
        ],
        timeout=10,
        step_name="step5_transform_with_jq",
    )
    if exit_code != 0:
        logger.error("step5_transform_with_jq failed exit_code=%s", exit_code)
    return exit_code


def main():
    logger.info("DATA ENGINEERING PIPELINE START main_pid=%s log_file=production.log", os.getpid())
    start_time = time.time()
    results = []

    try:
        code = step1_check_s3_object()
        results.append(("step1_check_s3_object", code))
    except Exception as e:
        logger.exception("step1_check_s3_object exception: %s", e)
        results.append(("step1_check_s3_object", 1))

    try:
        code = step2_download_from_s3()
        results.append(("step2_download_from_s3", code))
    except Exception as e:
        logger.exception("step2_download_from_s3 exception: %s", e)
        results.append(("step2_download_from_s3", 1))

    try:
        code = step3_list_s3_bucket()
        results.append(("step3_list_s3_bucket", code))
    except Exception as e:
        logger.exception("step3_list_s3_bucket exception: %s", e)
        results.append(("step3_list_s3_bucket", 1))

    try:
        code = step4_process_json_with_jq()
        results.append(("step4_process_json_with_jq", code))
    except Exception as e:
        logger.exception("step4_process_json_with_jq exception: %s", e)
        results.append(("step4_process_json_with_jq", 1))

    try:
        code = step5_transform_with_jq()
        results.append(("step5_transform_with_jq", code))
    except Exception as e:
        logger.exception("step5_transform_with_jq exception: %s", e)
        results.append(("step5_transform_with_jq", 1))

    elapsed = time.time() - start_time
    failed = [(name, code) for name, code in results if code != 0]

    logger.info("PIPELINE SUMMARY runtime_sec=%.2f failed=%s total=%s", elapsed, len(failed), len(results))
    for name, code in results:
        status = "FAILED" if code != 0 else "SUCCESS"
        logger.info("  %s: %s exit_code=%s", name, status, code)

    if failed:
        name, code = failed[0]
        logger.error("Exiting with code %s from %s", code, name)
        sys.exit(code)
    sys.exit(0)


if __name__ == "__main__":
    main()
