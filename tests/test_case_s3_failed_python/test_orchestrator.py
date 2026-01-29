"""
S3 Failed Python Demo Orchestrator.

Runs the pipeline and triggers RCA investigation on failure.
"""

import logging
import os
import sys
from datetime import UTC, datetime

from langsmith import traceable

from app.ingest import parse_grafana_payload
from app.main import _run
from tests.test_case_s3_failed_python import use_case
from tests.utils.alert_factory import create_alert
from tests.utils.langgraph_client import (
    fire_alert_to_langgraph,
    stream_investigation_results,
)

LOG_FILE = "production.log"
MAX_LOG_CHARS = 2000
MAX_LOG_LINES = 40


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _tail_log(log_file: str) -> str:
    if not os.path.exists(log_file):
        return ""
    with open(log_file, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    tail = "".join(lines[-MAX_LOG_LINES:])
    if len(tail) > MAX_LOG_CHARS:
        return tail[-MAX_LOG_CHARS:]
    return tail


def _format_failed_steps(results: list[dict]) -> str:
    failed_steps = []
    for result in results:
        if result.get("exit_code", 0) == 0:
            continue
        stderr_summary = result.get("stderr_summary", "")
        summary = f"{result.get('step_name')} exit_code={result.get('exit_code')}"
        if stderr_summary:
            summary = f"{summary} stderr={stderr_summary}"
        failed_steps.append(summary)
    return "; ".join(failed_steps)


def _build_alert_annotations(result: dict) -> dict:
    failed_steps = _format_failed_steps(result.get("results", []))
    log_excerpt = _tail_log(LOG_FILE)

    annotations = {
        "context_sources": "s3",
        "log_file": LOG_FILE,
        "runtime_sec": f"{result.get('runtime_sec', 0):.2f}",
        "failed_steps": failed_steps,
    }
    if log_excerpt:
        annotations["log_excerpt"] = log_excerpt
    return annotations


def main() -> int:
    _configure_logging()
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
        annotations=_build_alert_annotations(result),
    )

    print("Running investigation...")

    try:
        request = parse_grafana_payload(raw_alert)
        alert_name = request.alert_name
        pipeline_name = request.pipeline_name
        severity = request.severity
    except Exception:
        alert_name = f"Pipeline failure: {pipeline_name}"
        severity = "critical"

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
        try:
            response = fire_alert_to_langgraph(
                alert_name=alert_name,
                pipeline_name=pipeline_name,
                severity=severity,
                raw_alert=raw_alert,
                config_metadata={
                    "alert_id": raw_alert["alert_id"],
                    "pipeline_name": pipeline_name,
                    "run_id": run_id,
                    "log_file": LOG_FILE,
                },
            )
            stream_investigation_results(response)
            return {"status": response.status_code}
        except Exception as exc:
            print(f"LangGraph endpoint unavailable, running locally: {exc}")
            return _run(
                alert_name=alert_name,
                pipeline_name=pipeline_name,
                severity=severity,
                raw_alert=raw_alert,
            )

    run_with_alert_id()
    print(f"\n✓ Pipeline failed. Logs: {LOG_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
