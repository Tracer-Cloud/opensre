"""Seed a local Grafana+Loki demo stack with sample failure logs."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

LOCAL_LOKI_URL = "http://localhost:3100"
SERVICE_NAME = "prefect-etl-pipeline-local"
PIPELINE_NAME = "events_fact"
DEMO_RUN_ID = "local-events-fact-run-001"
DEMO_CORRELATION_ID = "local-events-fact-corr-001"


def wait_for_loki(timeout_seconds: int = 30) -> None:
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            response = requests.get(f"{LOCAL_LOKI_URL}/ready", timeout=2)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(1)
    raise SystemExit(
        "Local Loki is not ready. Start the stack with `make grafana-local-up` "
        f"and retry. Last error: {last_error}"
    )


def _pipeline_log_stream(now_ns: int) -> dict[str, Any]:
    base_labels = {
        "service_name": SERVICE_NAME,
        "pipeline_name": PIPELINE_NAME,
        "environment": "local",
        "stream_kind": "pipeline",
        "execution_run_id": DEMO_RUN_ID,
    }
    values = [
        [
            str(now_ns - 10_000_000_000),
            (
                f"run_id={DEMO_RUN_ID} correlation_id={DEMO_CORRELATION_ID} "
                "prefect-etl-pipeline-local starting scheduled run for events_fact"
            ),
        ],
        [
            str(now_ns - 8_000_000_000),
            (
                f"run_id={DEMO_RUN_ID} stage=extract correlation_id={DEMO_CORRELATION_ID} "
                "extract_events_fact fetched 128 source rows"
            ),
        ],
        [
            str(now_ns - 6_000_000_000),
            (
                f"run_id={DEMO_RUN_ID} stage=auth correlation_id={DEMO_CORRELATION_ID} "
                "extract_events_fact requesting Snowflake credentials from configured secret"
            ),
        ],
        [
            str(now_ns - 4_000_000_000),
            (
                f"run_id={DEMO_RUN_ID} stage=load correlation_id={DEMO_CORRELATION_ID} "
                "snowflake.connector.errors.DatabaseError: 250001 (08001): "
                "Failed to connect to DB: JWT token is invalid or expired"
            ),
        ],
        [
            str(now_ns - 2_000_000_000),
            (
                f"run_id={DEMO_RUN_ID} stage=load correlation_id={DEMO_CORRELATION_ID} "
                "events_fact pipeline aborted before the load step because Snowflake authentication failed"
            ),
        ],
    ]
    return {"stream": base_labels, "values": values}


def _supporting_log_stream(now_ns: int) -> dict[str, Any]:
    support_labels = {
        "service_name": SERVICE_NAME,
        "pipeline_name": PIPELINE_NAME,
        "environment": "local",
        "stream_kind": "supporting",
        "execution_run_id": DEMO_RUN_ID,
        "component": "warehouse-auth",
    }
    values = [
        [
            str(now_ns - 9_000_000_000),
            json.dumps({
                "event": "pipeline_context",
                "run_id": DEMO_RUN_ID,
                "correlation_id": DEMO_CORRELATION_ID,
                "dataset": PIPELINE_NAME,
                "warehouse": "analytics_wh",
                "upstream_rows": 128,
                "telemetry_source": "local_loki_seed",
            }, separators=(",", ":")),
        ],
        [
            str(now_ns - 5_000_000_000),
            json.dumps({
                "event": "credential_lookup",
                "run_id": DEMO_RUN_ID,
                "correlation_id": DEMO_CORRELATION_ID,
                "secret_name": "snowflake/service-account",
                "status": "stale_jwt",
                "message": "JWT presented to Snowflake had expired before connect",
            }, separators=(",", ":")),
        ],
        [
            str(now_ns - 3_000_000_000),
            json.dumps({
                "event": "pipeline_summary",
                "run_id": DEMO_RUN_ID,
                "correlation_id": DEMO_CORRELATION_ID,
                "status": "failed",
                "failed_stage": "load",
                "root_signal": "snowflake_authentication",
            }, separators=(",", ":")),
        ],
    ]
    return {"stream": support_labels, "values": values}


def build_log_streams(now_ns: int) -> list[dict[str, Any]]:
    """Build all seeded log streams for the local Grafana demo."""
    return [
        _pipeline_log_stream(now_ns),
        _supporting_log_stream(now_ns),
    ]


def seed_logs() -> None:
    wait_for_loki()
    payload = {"streams": build_log_streams(time.time_ns())}
    response = requests.post(
        f"{LOCAL_LOKI_URL}/loki/api/v1/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    response.raise_for_status()


def main() -> int:
    seed_logs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
