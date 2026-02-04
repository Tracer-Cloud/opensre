#!/usr/bin/env python3
"""Validate local code against Grafana Cloud endpoints.

This script:
1. Reads Grafana Cloud credentials from AWS Secrets Manager (tracer/grafana-cloud)
2. Runs pipelines locally but points to Grafana Cloud endpoints
3. Queries Grafana Cloud Mimir API for metrics
4. Queries Grafana Cloud Loki API for logs
5. Validates execution_run_id appears in both
6. Generates validation report
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import requests
from requests.auth import HTTPBasicAuth


def get_grafana_secrets() -> dict[str, str]:
    """Retrieve Grafana Cloud secrets from AWS Secrets Manager."""
    secrets_client = boto3.client("secretsmanager")
    try:
        response = secrets_client.get_secret_value(SecretId="tracer/grafana-cloud")
        secret_string = response["SecretString"]
        return json.loads(secret_string)
    except Exception as e:
        print(f"Failed to retrieve secrets: {e}")
        print("Falling back to environment variables...")
        return {
            "GCLOUD_HOSTED_METRICS_ID": os.getenv("GCLOUD_HOSTED_METRICS_ID", ""),
            "GCLOUD_HOSTED_METRICS_URL": os.getenv("GCLOUD_HOSTED_METRICS_URL", ""),
            "GCLOUD_HOSTED_LOGS_ID": os.getenv("GCLOUD_HOSTED_LOGS_ID", ""),
            "GCLOUD_HOSTED_LOGS_URL": os.getenv("GCLOUD_HOSTED_LOGS_URL", ""),
            "GCLOUD_HOSTED_TRACES_ID": os.getenv("GCLOUD_HOSTED_TRACES_ID", ""),
            "GCLOUD_HOSTED_TRACES_URL": os.getenv("GCLOUD_HOSTED_TRACES_URL", ""),
            "GCLOUD_RW_API_KEY": os.getenv("GCLOUD_RW_API_KEY", ""),
            "GCLOUD_OTLP_ENDPOINT": os.getenv("GCLOUD_OTLP_ENDPOINT", ""),
            "GCLOUD_OTLP_AUTH_HEADER": os.getenv("GCLOUD_OTLP_AUTH_HEADER", ""),
        }


def run_command(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run a shell command and return exit code, stdout, stderr."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, env=env, check=False
    )
    return result.returncode, result.stdout, result.stderr


def run_prefect_flow_with_cloud(secrets: dict[str, str]) -> str | None:
    """Run Prefect flow locally with Grafana Cloud endpoints."""
    print("Running Prefect flow locally with Grafana Cloud endpoints...")
    prefect_dir = Path(__file__).parent.parent / "test_case_upstream_prefect_ecs_fargate"

    env = os.environ.copy()
    env.update(secrets)
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = secrets.get("GCLOUD_OTLP_ENDPOINT", "")
    env["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={secrets.get('GCLOUD_OTLP_AUTH_HEADER', '')}"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"

    exit_code, stdout, stderr = run_command(
        ["python3", "-m", "tests.test_case_upstream_prefect_ecs_fargate.test_local"],
        cwd=prefect_dir,
        env=env,
    )
    if exit_code != 0:
        print("Prefect flow failed")
        if stderr:
            print(stderr.strip())
        return None

    match = re.search(r"flow-run/([0-9a-f-]+)", stdout + "\n" + stderr)
    return match.group(1) if match else None


def query_grafana_mimir_value(secrets: dict[str, str], query: str) -> float | None:
    """Query Grafana Cloud Mimir for a single metric value."""
    metrics_url = secrets.get("GCLOUD_HOSTED_METRICS_URL", "")
    metrics_id = secrets.get("GCLOUD_HOSTED_METRICS_ID", "")
    api_key = secrets.get("GCLOUD_RW_API_KEY", "")

    if not metrics_url or not metrics_id or not api_key:
        print("Missing Mimir credentials")
        return None

    try:
        response = requests.get(
            metrics_url.replace("/api/prom/push", "/api/prom/api/v1/query"),
            params={"query": query},
            auth=HTTPBasicAuth(metrics_id, api_key),
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json().get("data", {}).get("result", [])
            if not data:
                return 0.0
            return float(data[0].get("value", [0, "0"])[1])
    except requests.RequestException as e:
        print(f"Mimir query failed: {e}")
    return None


def query_grafana_loki_count(
    secrets: dict[str, str], query: str, lookback_seconds: int = 900
) -> int | None:
    """Query Grafana Cloud Loki for log count over a time range."""
    logs_url = secrets.get("GCLOUD_HOSTED_LOGS_URL", "")
    logs_id = secrets.get("GCLOUD_HOSTED_LOGS_ID", "")
    api_key = secrets.get("GCLOUD_RW_API_KEY", "")

    if not logs_url or not logs_id or not api_key:
        print("Missing Loki credentials")
        return None

    try:
        loki_query_url = logs_url.replace(
            "/loki/api/v1/push", "/loki/api/v1/query_range"
        )
        end_ns = int(time.time() * 1e9)
        start_ns = end_ns - int(lookback_seconds * 1e9)
        response = requests.get(
            loki_query_url,
            params={
                "query": query,
                "limit": 100,
                "start": str(start_ns),
                "end": str(end_ns),
            },
            auth=HTTPBasicAuth(logs_id, api_key),
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json().get("data", {}).get("result", [])
            return sum(len(r.get("values", [])) for r in data)
    except requests.RequestException as e:
        print(f"Loki query failed: {e}")
    return None


def _extract_span_names(trace_data: dict[str, Any]) -> set[str]:
    span_names: set[str] = set()
    for batch in trace_data.get("batches", []):
        for scope in batch.get("scopeSpans", []):
            for span in scope.get("spans", []):
                if span.get("name"):
                    span_names.add(span["name"])
        for scope in batch.get("instrumentationLibrarySpans", []):
            for span in scope.get("spans", []):
                if span.get("name"):
                    span_names.add(span["name"])
    return span_names


def _span_has_execution_run_id(span: dict[str, Any], execution_run_id: str) -> bool:
    for attr in span.get("attributes", []):
        if attr.get("key") == "execution.run_id":
            value = attr.get("value", {}).get("stringValue")
            return value == execution_run_id
    return False


def _trace_matches_run_id(trace_data: dict[str, Any], execution_run_id: str) -> bool:
    for batch in trace_data.get("batches", []):
        for scope in batch.get("scopeSpans", []):
            for span in scope.get("spans", []):
                if _span_has_execution_run_id(span, execution_run_id):
                    return True
        for scope in batch.get("instrumentationLibrarySpans", []):
            for span in scope.get("spans", []):
                if _span_has_execution_run_id(span, execution_run_id):
                    return True
    return False


def query_grafana_tempo_traces(
    secrets: dict[str, str],
    service_name: str,
    execution_run_id: str | None = None,
    lookback_seconds: int = 1800,
    limit: int = 20,
) -> tuple[int, set[str]]:
    """Query Grafana Cloud Tempo for traces and span names."""
    traces_url = secrets.get("GCLOUD_HOSTED_TRACES_URL", "")
    traces_id = secrets.get("GCLOUD_HOSTED_TRACES_ID", "")
    api_key = secrets.get("GCLOUD_RW_API_KEY", "")

    if not traces_url or not traces_id or not api_key:
        print("Missing Tempo credentials")
        return 0, set()

    search_url = traces_url.rstrip("/") + "/api/search"
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - int(lookback_seconds * 1e9)
    params: dict[str, Any] = {
        "limit": limit,
        "start": start_ns,
        "end": end_ns,
        "q": f'{{.service.name="{service_name}"}}',
    }

    try:
        response = requests.get(
            search_url,
            params=params,
            auth=HTTPBasicAuth(traces_id, api_key),
            timeout=10,
        )
        if response.status_code != 200:
            print(f"Tempo search failed: {response.status_code}")
            return 0, set()
        traces = response.json().get("traces", [])
    except requests.RequestException as e:
        print(f"Tempo search failed: {e}")
        return 0, set()

    span_names: set[str] = set()
    matched_traces = 0
    for trace in traces:
        trace_id = trace.get("traceID") or trace.get("traceId")
        if not trace_id:
            continue
        trace_url = traces_url.rstrip("/") + f"/api/traces/{trace_id}"
        try:
            detail_resp = requests.get(
                trace_url,
                auth=HTTPBasicAuth(traces_id, api_key),
                timeout=10,
            )
            if detail_resp.status_code != 200:
                continue
            trace_data = detail_resp.json()
            if execution_run_id and not _trace_matches_run_id(
                trace_data, execution_run_id
            ):
                continue
            matched_traces += 1
            span_names.update(_extract_span_names(trace_data))
        except requests.RequestException:
            continue

    return matched_traces, span_names


def validate_cloud_telemetry(
    secrets: dict[str, str],
    execution_run_id: str,
    metric_before: float | None,
    metric_after: float | None,
) -> dict[str, Any]:
    """Validate that telemetry appears in Grafana Cloud."""
    metric_delta = None
    if metric_before is not None and metric_after is not None:
        metric_delta = metric_after - metric_before

    results = {
        "execution_run_id": execution_run_id,
        "logs_found": False,
        "metrics_found": False,
        "traces_found": False,
        "log_count": 0,
        "metric_before": metric_before,
        "metric_after": metric_after,
        "metric_delta": metric_delta,
        "trace_count": 0,
        "span_names": [],
        "missing_spans": [],
    }

    print(f"Querying Grafana Cloud Loki for execution_run_id={execution_run_id}...")
    log_query = f'{{service_name="prefect-etl-pipeline"}} |= "{execution_run_id}"'
    log_count = query_grafana_loki_count(secrets, log_query)
    results["log_count"] = log_count or 0
    results["logs_found"] = bool(log_count and log_count > 0)

    print("Evaluating Grafana Cloud Mimir metrics delta...")
    results["metrics_found"] = metric_delta is not None and metric_delta >= 1

    print("Querying Grafana Cloud Tempo for traces...")
    expected_spans = {"extract_data", "validate_data", "transform_data", "load_data"}
    trace_count, span_names = query_grafana_tempo_traces(
        secrets,
        service_name="prefect-etl-pipeline",
        execution_run_id=execution_run_id,
    )
    results["trace_count"] = trace_count
    results["traces_found"] = trace_count > 0
    results["span_names"] = sorted(span_names)
    results["missing_spans"] = sorted(expected_spans - span_names)

    return results


def generate_report(results: list[dict[str, Any]]) -> None:
    """Generate validation report."""
    print("\n" + "=" * 60)
    print("GRAFANA CLOUD VALIDATION REPORT")
    print("=" * 60)

    for result in results:
        print(f"\nExecution Run ID: {result['execution_run_id']}")
        print(f"  Logs: {'✓' if result['logs_found'] else '✗'} ({result['log_count']} found)")
        print(
            "  Metrics: "
            f"{'✓' if result['metrics_found'] else '✗'} "
            f"(delta={result['metric_delta']})"
        )
        print(
            f"  Traces: {'✓' if result['traces_found'] else '✗'} ({result['trace_count']} found)"
        )
        if result["span_names"]:
            print(f"  Span names: {', '.join(result['span_names'])}")
        if result["missing_spans"]:
            print(f"  Missing spans: {', '.join(result['missing_spans'])}")

    all_passed = all(
        r["logs_found"] and r["metrics_found"] and r["traces_found"]
        for r in results
    )

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL VALIDATIONS PASSED")
    else:
        print("✗ SOME VALIDATIONS FAILED")
    print("=" * 60)


def main() -> int:
    """Main entry point."""
    print("Retrieving Grafana Cloud credentials...")
    secrets = get_grafana_secrets()

    required_vars = [
        "GCLOUD_OTLP_ENDPOINT",
        "GCLOUD_OTLP_AUTH_HEADER",
        "GCLOUD_HOSTED_METRICS_URL",
        "GCLOUD_HOSTED_METRICS_ID",
        "GCLOUD_HOSTED_LOGS_URL",
        "GCLOUD_HOSTED_LOGS_ID",
        "GCLOUD_HOSTED_TRACES_URL",
        "GCLOUD_HOSTED_TRACES_ID",
        "GCLOUD_RW_API_KEY",
    ]

    missing = [var for var in required_vars if not secrets.get(var)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        return 1

    print("Running test pipelines with Grafana Cloud endpoints...")

    execution_run_ids = []

    metric_query = (
        'pipeline_runs_total{pipeline_name="upstream_downstream_pipeline_prefect",'
        'status="success"}'
    )
    metric_before = query_grafana_mimir_value(secrets, metric_query)

    run_id = run_prefect_flow_with_cloud(secrets)
    if run_id:
        execution_run_ids.append(run_id)
        print("Waiting for telemetry export...")
        time.sleep(15)

    metric_after = query_grafana_mimir_value(secrets, metric_query)

    results = []
    for run_id in execution_run_ids:
        result = validate_cloud_telemetry(
            secrets,
            run_id,
            metric_before,
            metric_after,
        )
        results.append(result)

    generate_report(results)

    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
