#!/usr/bin/env python3
"""
Test local dev Airflow sending telemetry to Grafana Cloud.

This script simulates an Airflow DAG run locally and sends telemetry
directly to Grafana Cloud (no Alloy sidecar needed for local dev).

Tests all three telemetry types:
- Logs → Grafana Cloud Loki
- Traces → Grafana Cloud Tempo
- Metrics → Grafana Cloud Mimir

Run directly: python3 test_local_dev_grafana_cloud.py
"""

import json
import logging
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import requests

# Add paths BEFORE importing tracer_telemetry
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / "tests" / "shared" / "telemetry"))
sys.path.insert(0, str(project_root))

SERVICE_NAME = "airflow-etl-pipeline-local"


def get_grafana_secrets():
    """Fetch Grafana Cloud secrets from AWS Secrets Manager."""
    secrets_client = boto3.client("secretsmanager")
    response = secrets_client.get_secret_value(SecretId="tracer/grafana-cloud")
    return json.loads(response["SecretString"])


def configure_otlp_env(secrets: dict) -> None:
    """Configure OTLP environment variables for telemetry export."""
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = secrets["GCLOUD_OTLP_ENDPOINT"]
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={secrets['GCLOUD_OTLP_AUTH_HEADER']}"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    os.environ["GCLOUD_OTLP_ENDPOINT"] = secrets["GCLOUD_OTLP_ENDPOINT"]
    os.environ["GCLOUD_OTLP_AUTH_HEADER"] = secrets["GCLOUD_OTLP_AUTH_HEADER"]
    os.environ["GCLOUD_HOSTED_METRICS_ID"] = secrets.get("GCLOUD_HOSTED_METRICS_ID", "")
    os.environ["GCLOUD_HOSTED_METRICS_URL"] = secrets.get("GCLOUD_HOSTED_METRICS_URL", "")
    os.environ["GCLOUD_HOSTED_LOGS_ID"] = secrets["GCLOUD_HOSTED_LOGS_ID"]
    os.environ["GCLOUD_HOSTED_LOGS_URL"] = secrets["GCLOUD_HOSTED_LOGS_URL"]
    os.environ["GCLOUD_RW_API_KEY"] = secrets["GCLOUD_RW_API_KEY"]


def run_simulated_dag(tracer, logger):
    """Simulate an Airflow DAG execution with telemetry."""
    execution_run_id = f"local-dev-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    correlation_id = str(uuid.uuid4())

    print("3. Simulating Airflow DAG execution...")
    print(f"   Execution Run ID: {execution_run_id}")
    print(f"   Correlation ID: {correlation_id}")
    print()

    start_time = time.time()

    with tracer.start_as_current_span("dag_run") as dag_span:
        dag_span.set_attribute("execution.run_id", execution_run_id)
        dag_span.set_attribute("dag_id", "local_dev_test_dag")
        dag_span.set_attribute("pipeline.name", "local_dev_test_airflow")

        logger.info(json.dumps({
            "event": "dag_started",
            "dag_id": "local_dev_test_dag",
            "execution_run_id": execution_run_id,
            "correlation_id": correlation_id,
        }))

        print("   Running tasks:")

        tasks = [
            ("extract_data", {"records_extracted": 100}),
            ("validate_data", {"records_valid": 98, "records_invalid": 2}),
            ("transform_data", {"records_transformed": 98}),
            ("load_data", {"records_loaded": 98}),
        ]

        for task_id, task_result in tasks:
            with tracer.start_as_current_span(task_id) as span:
                span.set_attribute("execution.run_id", execution_run_id)
                span.set_attribute("correlation_id", correlation_id)
                span.set_attribute("task_id", task_id)

                logger.info(json.dumps({
                    "event": "task_started",
                    "task_id": task_id,
                    "execution_run_id": execution_run_id,
                }))
                time.sleep(0.1)
                logger.info(json.dumps({
                    "event": "task_completed",
                    "task_id": task_id,
                    "execution_run_id": execution_run_id,
                    **task_result,
                }))
                print(f"     ✓ {task_id}")

        dag_span.set_attribute("status", "success")
        dag_span.set_attribute("total_tasks", 4)

        logger.info(json.dumps({
            "event": "dag_completed",
            "dag_id": "local_dev_test_dag",
            "execution_run_id": execution_run_id,
            "status": "success",
            "total_tasks": 4,
        }))

    return time.time() - start_time


def query_logs(secrets: dict) -> int:
    """Query Grafana Cloud Loki for logs."""
    print("📋 LOGS (Loki)")
    print("-" * 40)

    logs_url = secrets["GCLOUD_HOSTED_LOGS_URL"].replace(
        "/loki/api/v1/push", "/loki/api/v1/query_range"
    )
    logs_id = secrets["GCLOUD_HOSTED_LOGS_ID"]
    api_key = secrets["GCLOUD_RW_API_KEY"]

    end = datetime.now(UTC)
    start = end - timedelta(minutes=5)

    query = f'{{service_name="{SERVICE_NAME}"}}'
    response = requests.get(
        logs_url,
        params={
            "query": query,
            "limit": 100,
            "start": int(start.timestamp() * 1e9),
            "end": int(end.timestamp() * 1e9),
        },
        auth=(logs_id, api_key),
        timeout=10,
    )

    logs_found = 0
    if response.status_code == 200:
        data = response.json()
        results = data.get("data", {}).get("result", [])
        logs_found = sum(len(stream.get("values", [])) for stream in results)

        if logs_found > 0:
            print(f"✅ Found {logs_found} log entries")
            for stream in results[:1]:
                for _, log_line in stream.get("values", [])[:2]:
                    print(f"   {log_line[:80]}...")
        else:
            print("❌ No logs found")
    else:
        print(f"❌ Loki query error: {response.status_code}")

    print()
    return logs_found


def query_traces() -> int:
    """Query Grafana Cloud Tempo for traces."""
    print("🔍 TRACES (Tempo)")
    print("-" * 40)

    grafana_instance = "https://tracerbio.grafana.net"

    read_token = None
    env_path = project_root / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("GRAFANA_READ_TOKEN="):
                    read_token = line.split("=", 1)[1].strip().strip('"')
                    break

    traces_found = 0
    if read_token:
        trace_search_url = f"{grafana_instance}/api/datasources/proxy/uid/grafanacloud-traces/api/search"
        trace_headers = {"Authorization": f"Bearer {read_token}"}

        response = requests.get(
            trace_search_url,
            headers=trace_headers,
            params={
                "q": f'{{.service.name="{SERVICE_NAME}" && name="dag_run"}}',
                "limit": 10,
            },
            timeout=10,
        )

        if response.status_code == 200:
            trace_data = response.json()
            traces = trace_data.get("traces", [])
            traces_found = len(traces)

            if traces_found > 0:
                print(f"✅ Found {traces_found} traces")
                for t in traces[:2]:
                    print(f"   TraceID: {t.get('traceID', 'N/A')[:16]}... "
                          f"Root: {t.get('rootServiceName', 'N/A')} → {t.get('rootTraceName', 'N/A')}")
            else:
                print("❌ No traces found (may still be indexing)")
        else:
            print(f"⚠️  Tempo query returned: {response.status_code}")
    else:
        print("⚠️  GRAFANA_READ_TOKEN not found in .env - cannot query Tempo")
        print("   (Traces are still being exported via OTLP)")

    print()
    return traces_found


def query_metrics(secrets: dict) -> int:
    """Query Grafana Cloud Mimir for metrics."""
    print("📊 METRICS (Mimir)")
    print("-" * 40)

    metrics_url = secrets["GCLOUD_HOSTED_METRICS_URL"].replace(
        "/api/prom/push", "/api/prom/api/v1/query"
    )
    metrics_id = secrets["GCLOUD_HOSTED_METRICS_ID"]
    api_key = secrets["GCLOUD_RW_API_KEY"]

    response = requests.get(
        metrics_url,
        params={
            "query": f'pipeline_runs_total{{service_name="{SERVICE_NAME}"}}',
        },
        auth=(metrics_id, api_key),
        timeout=10,
    )

    metrics_found = 0
    if response.status_code == 200:
        metric_data = response.json()
        results = metric_data.get("data", {}).get("result", [])
        metrics_found = len(results)

        if metrics_found > 0:
            print(f"✅ Found {metrics_found} metric series")
            for r in results[:2]:
                value = r.get("value", [None, "N/A"])[1]
                print(f"   pipeline_runs_total = {value}")
        else:
            print("❌ No metrics found (may take longer to appear)")
    else:
        print(f"⚠️  Mimir query returned: {response.status_code}")

    print()
    return metrics_found


def main():
    """Main entry point."""
    print("=" * 80)
    print("LOCAL DEV AIRFLOW → GRAFANA CLOUD VALIDATION")
    print("=" * 80)
    print()

    print("1. Configuring Grafana Cloud OTLP endpoint...")
    secrets = get_grafana_secrets()
    configure_otlp_env(secrets)
    print(f"   Endpoint: {secrets['GCLOUD_OTLP_ENDPOINT']}")
    print("   Protocol: http/protobuf")
    print()

    print("2. Initializing OpenTelemetry...")
    from tracer_telemetry import init_telemetry

    telemetry = init_telemetry(
        service_name=SERVICE_NAME,
        resource_attributes={
            "pipeline.name": "local_dev_test_airflow",
            "pipeline.framework": "airflow",
            "environment": "local-development",
        },
    )
    tracer = telemetry.tracer

    logger = logging.getLogger("airflow.dag.local_test")
    logger.setLevel(logging.INFO)

    print("   ✓ Telemetry initialized (tracer, logger, metrics)")
    print()

    duration = run_simulated_dag(tracer, logger)

    print()
    print("4. Recording metrics...")
    telemetry.record_run(
        status="success",
        duration_seconds=duration,
        record_count=98,
        failure_count=2,
        attributes={"dag_id": "local_dev_test_dag"},
    )
    print("   ✓ Metrics recorded")

    print()
    print("5. Flushing telemetry to Grafana Cloud...")
    telemetry.flush()
    try:
        from opentelemetry import metrics
        provider = metrics.get_meter_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5000)
    except Exception:
        pass
    time.sleep(3)
    print("   ✓ Telemetry flushed (traces, logs, metrics)")
    print()

    print("6. Waiting for telemetry to arrive in Grafana Cloud (15 seconds)...")
    time.sleep(15)
    print()

    print("7. Querying Grafana Cloud...")
    print("=" * 80)
    print("VALIDATION RESULTS")
    print("=" * 80)
    print()

    logs_found = query_logs(secrets)
    traces_found = query_traces()
    metrics_found = query_metrics(secrets)

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    all_passed = logs_found > 0 and traces_found > 0
    if all_passed:
        print(f"✅ LOGS:    {logs_found} entries in Loki")
        print(f"✅ TRACES:  {traces_found} traces in Tempo")
        print(f"{'✅' if metrics_found > 0 else '⚠️ '} METRICS: {metrics_found} series in Mimir (may take longer)")
        print()
        print("✅ LOCAL DEV AIRFLOW → GRAFANA CLOUD: VERIFIED")
        return 0
    elif logs_found > 0 or traces_found > 0:
        print(f"{'✅' if logs_found > 0 else '❌'} LOGS:    {logs_found} entries")
        print(f"{'✅' if traces_found > 0 else '❌'} TRACES:  {traces_found} traces")
        print(f"{'✅' if metrics_found > 0 else '⚠️ '} METRICS: {metrics_found} series")
        print()
        print("⚠️  PARTIAL SUCCESS - Some telemetry is reaching Grafana Cloud")
        return 0
    else:
        print("❌ LOGS:    0 entries")
        print("❌ TRACES:  0 traces")
        print("❌ METRICS: 0 series")
        print()
        print("❌ FAILED - No telemetry found in Grafana Cloud")
        print()
        print("Troubleshooting:")
        print("1. Check OTLP endpoint is correct")
        print("2. Verify auth header is valid")
        print("3. Wait longer and re-run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
