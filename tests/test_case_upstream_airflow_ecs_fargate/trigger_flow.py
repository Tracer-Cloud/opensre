#!/usr/bin/env python3
"""Trigger the Airflow DAG for testing."""

from datetime import UTC, datetime
import os

import requests

AIRFLOW_API_URL = os.getenv("AIRFLOW_API_URL", "http://127.0.0.1:8080")
AIRFLOW_USERNAME = os.getenv("AIRFLOW_API_USERNAME", "admin")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_API_PASSWORD", "admin")
AIRFLOW_DAG_ID = os.getenv("AIRFLOW_DAG_ID", "upstream_downstream_pipeline_airflow")
LANDING_BUCKET = os.getenv("LANDING_BUCKET", "landing-bucket")
S3_KEY = os.getenv("S3_KEY", "ingested/sample/data.json")
INJECT_ERROR = os.getenv("INJECT_ERROR", "true").lower() == "true"


def _airflow_base_url() -> str:
    api_url = AIRFLOW_API_URL.rstrip("/")
    if "/api/" in api_url:
        return api_url.split("/api/", 1)[0]
    return api_url


def _airflow_api_url() -> str:
    return f"{_airflow_base_url()}/api/v2"


def _get_airflow_token() -> str:
    token_url = f"{_airflow_base_url()}/auth/token"

    response = requests.get(token_url, timeout=10)
    if response.ok:
        return response.json().get("access_token", "")

    response = requests.post(
        token_url,
        json={"username": AIRFLOW_USERNAME, "password": AIRFLOW_PASSWORD},
        timeout=10,
    )
    if response.status_code not in (200, 201):
        response.raise_for_status()
    return response.json().get("access_token", "")


def main(inject_error: bool | None = None) -> None:
    if inject_error is None:
        inject_error = INJECT_ERROR

    run_id = f"manual__{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "dag_run_id": run_id,
        "logical_date": datetime.now(UTC).isoformat(),
        "conf": {
            "bucket": LANDING_BUCKET,
            "key": S3_KEY,
            "inject_error": inject_error,
        },
    }

    token = _get_airflow_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    response = requests.post(
        f"{_airflow_api_url()}/dags/{AIRFLOW_DAG_ID}/dagRuns",
        json=payload,
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    print(f"✅ Triggered DAG run: {run_id}")


if __name__ == "__main__":
    main()
