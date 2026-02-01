#!/usr/bin/env python3
"""Trigger an Airflow DAG run with a specific S3 input."""

from datetime import UTC, datetime

import requests

AIRFLOW_API_URL = "http://127.0.0.1:8080/api/v2"
AIRFLOW_USERNAME = "admin"
AIRFLOW_PASSWORD = "admin"
DAG_ID = "upstream_downstream_pipeline_airflow"

BUCKET = "tracerairflowecsfargate-landingbucket23fe90fb-woehzac5msvj"
KEY = "ingested/20260131-124548/data.json"


def _airflow_base_url() -> str:
    api_url = AIRFLOW_API_URL.rstrip("/")
    if "/api/" in api_url:
        return api_url.split("/api/", 1)[0]
    return api_url


def _get_airflow_token() -> str:
    base_url = _airflow_base_url()
    token_url = f"{base_url}/auth/token"

    response = requests.get(token_url, timeout=10)
    if response.ok:
        return response.json().get("access_token", "")

    response = requests.post(
        token_url,
        json={"username": AIRFLOW_USERNAME, "password": AIRFLOW_PASSWORD},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("access_token", "")


def main() -> None:
    run_id = f"manual__{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "dag_run_id": run_id,
        "logical_date": datetime.now(UTC).isoformat(),
        "conf": {"bucket": BUCKET, "key": KEY},
    }
    token = _get_airflow_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    response = requests.post(
        f"{AIRFLOW_API_URL}/dags/{DAG_ID}/dagRuns",
        json=payload,
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    print(f"✅ Triggered DAG run: {run_id}")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Trigger an Airflow DAG run with a specific S3 input."""



AIRFLOW_API_URL = "http://127.0.0.1:8080/api/v1"
AIRFLOW_USERNAME = "admin"
AIRFLOW_PASSWORD = "admin"
DAG_ID = "upstream_downstream_pipeline_airflow"

BUCKET = "tracerairflowecsfargate-landingbucket23fe90fb-woehzac5msvj"
KEY = "ingested/20260131-124548/data.json"


def main() -> None:
    run_id = f"manual__{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    payload = {"dag_run_id": run_id, "conf": {"bucket": BUCKET, "key": KEY}}

    response = requests.post(
        f"{AIRFLOW_API_URL}/dags/{DAG_ID}/dagRuns",
        auth=(AIRFLOW_USERNAME, AIRFLOW_PASSWORD),
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    print(f"✅ Triggered DAG run: {run_id}")


if __name__ == "__main__":
    main()
