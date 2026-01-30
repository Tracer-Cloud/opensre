"""
Command runner utilities (stateless).

Generic utilities for running system binaries/commands via subprocess.
"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

MAX_LINE = 20000


def _first_line(text: str) -> str:
    """Extract first line of text, truncated to MAX_LINE."""
    if not text:
        return ""
    return text.splitlines()[0][:MAX_LINE]


def run_tool(cmd: list[str], timeout: int = 10, step_name: str = "") -> dict:
    """
    Run CLI tool and return result metadata (stateless).

    Args:
        cmd: Command as list of strings (e.g., ["aws", "s3", "ls"])
        timeout: Timeout in seconds
        step_name: Optional step name for logging

    Returns:
        Dict with step_name, command, exit_code, stderr_summary, stdout_summary
    """
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
        logger.info(
            "step=%s exit_code=%s stdout_len=%s",
            step_name,
            exit_code,
            len(out_decoded),
        )
        for line in out_decoded.strip().splitlines():
            logger.info("stdout: %s", line[:MAX_LINE] if len(line) > MAX_LINE else line)
    if err_decoded.strip():
        logger.error(
            "step=%s exit_code=%s stderr_len=%s",
            step_name,
            exit_code,
            len(err_decoded),
        )
        for line in err_decoded.strip().splitlines():
            logger.error("stderr: %s", line[:MAX_LINE] if len(line) > MAX_LINE else line)

    logger.info("step=%s exit_code=%s", step_name, exit_code)
    return {
        "step_name": step_name,
        "command": cmd_str,
        "exit_code": exit_code,
        "stderr_summary": _first_line(err_decoded.strip()),
        "stdout_summary": _first_line(out_decoded.strip()),
    }
