#!/usr/bin/env python3
"""Trigger the Airflow DAG using the Airflow 3.1.6 REST API."""

from datetime import UTC, datetime

import requests

AIRFLOW_API_URL = "http://127.0.0.1:8080/api/v1"
AIRFLOW_USERNAME = "admin"
AIRFLOW_PASSWORD = "admin"
DAG_ID = "upstream_downstream_pipeline_airflow"

BUCKET = "tracerairflowecsfargate-landingbucket23fe90fb-woehzac5msvj"
KEY = "ingested/20260131-124548/data.json"


def trigger_run(inject_error: bool = True) -> None:
    run_id = f"manual__{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "dag_run_id": run_id,
        "conf": {"bucket": BUCKET, "key": KEY, "inject_error": inject_error},
    }

    response = requests.post(
        f"{AIRFLOW_API_URL}/dags/{DAG_ID}/dagRuns",
        auth=(AIRFLOW_USERNAME, AIRFLOW_PASSWORD),
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    print(f"✅ Triggered DAG run: {run_id}")


if __name__ == "__main__":
    trigger_run()
#!/usr/bin/env python3
"""Trigger the Airflow DAG using the Airflow 3.1.6 REST API."""

from datetime import UTC, datetime

import requests

AIRFLOW_API_URL = "http://127.0.0.1:8080/api/v1"
AIRFLOW_USERNAME = "admin"
AIRFLOW_PASSWORD = "admin"
DAG_ID = "upstream_downstream_pipeline_airflow"

BUCKET = "tracerairflowecsfargate-landingbucket23fe90fb-woehzac5msvj"
KEY = "ingested/20260131-124548/data.json"


def trigger_run(inject_error: bool = True) -> None:
    run_id = f"manual__{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "dag_run_id": run_id,
        "conf": {"bucket": BUCKET, "key": KEY, "inject_error": inject_error},
    }

    response = requests.post(
        f"{AIRFLOW_API_URL}/dags/{DAG_ID}/dagRuns",
        auth=(AIRFLOW_USERNAME, AIRFLOW_PASSWORD),
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    print(f"✅ Triggered DAG run: {run_id}")


if __name__ == "__main__":
    trigger_run()
