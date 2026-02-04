#!/usr/bin/env python3
"""Validate Grafana Cloud telemetry for prefect-etl-pipeline."""

import os
import sys
import time
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class GrafanaCloud:
    REQUIRED_ENV = [
        "GCLOUD_HOSTED_METRICS_URL",
        "GCLOUD_HOSTED_METRICS_ID",
        "GCLOUD_HOSTED_LOGS_URL",
        "GCLOUD_HOSTED_LOGS_ID",
        "GCLOUD_HOSTED_TRACES_URL_TEMPO",
        "GCLOUD_HOSTED_TRACES_ID",
        "GCLOUD_RW_API_KEY",
    ]

    def __init__(self, service_name="prefect-etl-pipeline", lookback_seconds=900):
        load_env()
        self.service_name = service_name
        self.lookback_seconds = lookback_seconds
        self.env = {key: os.getenv(key) for key in self.REQUIRED_ENV}

    def missing_env(self):
        return [key for key, value in self.env.items() if not value]

    def _metrics_query_url(self):
        metrics_url = self.env["GCLOUD_HOSTED_METRICS_URL"]
        if "/api/prom/push" in metrics_url:
            return metrics_url.replace("/api/prom/push", "/api/prom/api/v1/query")
        return metrics_url.rstrip("/") + "/api/prom/api/v1/query"

    def _logs_query_url(self):
        logs_url = self.env["GCLOUD_HOSTED_LOGS_URL"]
        if "/loki/api/v1/push" in logs_url:
            return logs_url.replace("/loki/api/v1/push", "/loki/api/v1/query_range")
        return logs_url.rstrip("/") + "/loki/api/v1/query_range"

    def _traces_search_url(self):
        return self.env["GCLOUD_HOSTED_TRACES_URL_TEMPO"].rstrip("/") + "/api/search"

    def check_metrics(self):
        query = f'pipeline_runs_total{{service_name="{self.service_name}"}}'
        try:
            response = requests.get(
                self._metrics_query_url(),
                params={"query": query},
                auth=HTTPBasicAuth(
                    self.env["GCLOUD_HOSTED_METRICS_ID"],
                    self.env["GCLOUD_RW_API_KEY"],
                ),
                timeout=2,
            )
        except requests.RequestException as exc:
            return False, str(exc)

        if response.status_code != 200:
            return False, f"{response.status_code} {response.text[:200]}".strip()
        result = response.json().get("data", {}).get("result", [])
        if not result:
            return False, "no metric series found"
        return True, ""

    def check_logs(self):
        end_ns = int(time.time() * 1e9)
        start_ns = end_ns - int(self.lookback_seconds * 1e9)
        query = f'{{service_name="{self.service_name}"}}'
        try:
            response = requests.get(
                self._logs_query_url(),
                params={
                    "query": query,
                    "limit": 100,
                    "start": str(start_ns),
                    "end": str(end_ns),
                },
                auth=HTTPBasicAuth(
                    self.env["GCLOUD_HOSTED_LOGS_ID"],
                    self.env["GCLOUD_RW_API_KEY"],
                ),
                timeout=2,
            )
        except requests.RequestException as exc:
            return False, str(exc)

        if response.status_code != 200:
            return False, f"{response.status_code} {response.text[:200]}".strip()
        streams = response.json().get("data", {}).get("result", [])
        count = sum(len(stream.get("values", [])) for stream in streams)
        if count <= 0:
            return False, "no logs found"
        return True, ""

    def check_traces(self):
        end_s = int(time.time())
        start_s = end_s - self.lookback_seconds
        params = {
            "limit": 5,
            "start": start_s,
            "end": end_s,
            "q": f'{{resource.service.name="{self.service_name}"}}',
        }
        try:
            response = requests.get(
                self._traces_search_url(),
                params=params,
                auth=HTTPBasicAuth(
                    self.env["GCLOUD_HOSTED_TRACES_ID"],
                    self.env["GCLOUD_RW_API_KEY"],
                ),
                timeout=2,
            )
        except requests.RequestException as exc:
            return False, str(exc)

        if response.status_code != 200:
            return False, f"{response.status_code} {response.text[:200]}".strip()
        traces = response.json().get("traces", [])
        if not traces:
            return False, "no traces found"
        return True, ""


def main():
    client = GrafanaCloud()
    missing = client.missing_env()
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        return 1

    results = {
        "logs": client.check_logs(),
        "metrics": client.check_metrics(),
        "traces": client.check_traces(),
    }

    all_ok = True
    for name in ("logs", "metrics", "traces"):
        ok, detail = results.get(name, (False, "no response"))
        if ok:
            print(f"✅ {name}")
        else:
            all_ok = False
            message = f"❌ {name} {detail}".strip()
            print(message)

    if all_ok:
        print("✅ Grafana Cloud ingestion detected")
        return 0
    print("❌ One or more checks failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
