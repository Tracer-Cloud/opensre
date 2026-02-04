#!/usr/bin/env python3
"""Validate Grafana Cloud endpoints (Loki, Mimir, Tempo)."""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def require_env(keys):
    missing = [key for key in keys if not os.getenv(key)]
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def metrics_query_url(metrics_url):
    if "/api/prom/push" in metrics_url:
        return metrics_url.replace("/api/prom/push", "/api/prom/api/v1/query")
    return metrics_url.rstrip("/") + "/api/prom/api/v1/query"


def logs_labels_url(logs_url):
    if "/loki/api/v1/push" in logs_url:
        return logs_url.replace("/loki/api/v1/push", "/loki/api/v1/labels")
    return logs_url.rstrip("/") + "/loki/api/v1/labels"


def check_metrics(env):
    url = metrics_query_url(env["GCLOUD_HOSTED_METRICS_URL"])
    return requests.get(
        url,
        params={"query": "1"},
        auth=HTTPBasicAuth(env["GCLOUD_HOSTED_METRICS_ID"], env["GCLOUD_RW_API_KEY"]),
        timeout=2,
    )


def check_logs(env):
    url = logs_labels_url(env["GCLOUD_HOSTED_LOGS_URL"])
    return requests.get(
        url,
        auth=HTTPBasicAuth(env["GCLOUD_HOSTED_LOGS_ID"], env["GCLOUD_RW_API_KEY"]),
        timeout=2,
    )


def check_traces(env):
    url = env["GCLOUD_HOSTED_TRACES_URL_TEMPO"].rstrip("/") + "/api/search"
    end_s = int(time.time())
    start_s = end_s - 900
    params = {
        "limit": 1,
        "start": start_s,
        "end": end_s,
        "q": '{resource.service.name="prefect-etl-pipeline"}',
    }
    return requests.get(
        url,
        params=params,
        auth=HTTPBasicAuth(env["GCLOUD_HOSTED_TRACES_ID"], env["GCLOUD_RW_API_KEY"]),
        timeout=2,
    )


def run_check(name, func, env):
    try:
        response = func(env)
    except requests.RequestException as exc:
        return name, False, str(exc)
    if response.status_code == 200:
        return name, True, ""
    detail = response.text.strip().replace("\n", " ")
    return name, False, f"{response.status_code} {detail[:200]}".strip()


def main():
    load_env()
    required = [
        "GCLOUD_HOSTED_METRICS_URL",
        "GCLOUD_HOSTED_METRICS_ID",
        "GCLOUD_HOSTED_LOGS_URL",
        "GCLOUD_HOSTED_LOGS_ID",
        "GCLOUD_HOSTED_TRACES_URL_TEMPO",
        "GCLOUD_HOSTED_TRACES_ID",
        "GCLOUD_RW_API_KEY",
    ]
    require_env(required)
    env = {key: os.getenv(key) for key in required}

    checks = {
        "metrics": check_metrics,
        "logs": check_logs,
        "traces": check_traces,
    }

    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(run_check, name, func, env): name
            for name, func in checks.items()
        }
        for future in as_completed(futures):
            name, ok, detail = future.result()
            results[name] = (ok, detail)

    all_ok = True
    for name in ("logs", "metrics", "traces"):
        ok, detail = results.get(name, (False, "no response"))
        if ok:
            print(f"✅ {name}")
        else:
            all_ok = False
            print(f"❌ {name} {detail}".strip())

    if all_ok:
        print("✅ Grafana Cloud endpoints reachable")
        return 0
    print("❌ One or more endpoints failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
