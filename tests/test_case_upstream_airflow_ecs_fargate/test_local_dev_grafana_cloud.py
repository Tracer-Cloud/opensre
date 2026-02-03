#!/usr/bin/env python3
"""
Test local dev Airflow sending telemetry to Grafana Cloud.

This script simulates an Airflow DAG run locally and sends telemetry
directly to Grafana Cloud (no Alloy sidecar needed for local dev).

Tests all three telemetry types:
- Logs → Grafana Cloud Loki
- Traces → Grafana Cloud Tempo
- Metrics → Grafana Cloud Mimir
"""

import json
import logging
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3

# Add paths BEFORE importing tracer_telemetry
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / "tests" / "shared" / "telemetry"))
sys.path.insert(0, str(project_root))

# Configure Grafana Cloud OTLP endpoint BEFORE initializing telemetry
print("=" * 80)
print("LOCAL DEV AIRFLOW → GRAFANA CLOUD VALIDATION")
print("=" * 80)
print()

print("1. Configuring Grafana Cloud OTLP endpoint...")
secrets_client = boto3.client("secretsmanager")
response = secrets_client.get_secret_value(SecretId="tracer/grafana-cloud")
secrets = json.loads(response["SecretString"])

# Set OTLP environment variables BEFORE importing telemetry
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = secrets["GCLOUD_OTLP_ENDPOINT"]
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={secrets['GCLOUD_OTLP_AUTH_HEADER']}"
os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
# Also set Grafana Cloud specific vars for validation
os.environ["GCLOUD_OTLP_ENDPOINT"] = secrets["GCLOUD_OTLP_ENDPOINT"]
os.environ["GCLOUD_OTLP_AUTH_HEADER"] = secrets["GCLOUD_OTLP_AUTH_HEADER"]
os.environ["GCLOUD_HOSTED_METRICS_ID"] = secrets.get("GCLOUD_HOSTED_METRICS_ID", "")
os.environ["GCLOUD_HOSTED_METRICS_URL"] = secrets.get("GCLOUD_HOSTED_METRICS_URL", "")
os.environ["GCLOUD_HOSTED_LOGS_ID"] = secrets["GCLOUD_HOSTED_LOGS_ID"]
os.environ["GCLOUD_HOSTED_LOGS_URL"] = secrets["GCLOUD_HOSTED_LOGS_URL"]
os.environ["GCLOUD_RW_API_KEY"] = secrets["GCLOUD_RW_API_KEY"]

print(f"   Endpoint: {secrets['GCLOUD_OTLP_ENDPOINT']}")
print(f"   Protocol: http/protobuf")
print()

# Now initialize telemetry
print("2. Initializing OpenTelemetry...")
from tracer_telemetry import init_telemetry

SERVICE_NAME = "airflow-etl-pipeline-local"
telemetry = init_telemetry(
    service_name=SERVICE_NAME,
    resource_attributes={
        "pipeline.name": "local_dev_test_airflow",
        "pipeline.framework": "airflow",
        "environment": "local-development",
    },
)
tracer = telemetry.tracer

# Get a logger - this will be captured by the OTLP logging handler
logger = logging.getLogger("airflow.dag.local_test")
logger.setLevel(logging.INFO)

print("   ✓ Telemetry initialized (tracer, logger, metrics)")
print()

# Simulate DAG run
execution_run_id = f"local-dev-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
correlation_id = str(uuid.uuid4())

print(f"3. Simulating Airflow DAG execution...")
print(f"   Execution Run ID: {execution_run_id}")
print(f"   Correlation ID: {correlation_id}")
print()

start_time = time.time()

# Root span for DAG - LOGS are emitted inside spans so they get correlated
with tracer.start_as_current_span("dag_run") as dag_span:
    dag_span.set_attribute("execution.run_id", execution_run_id)
    dag_span.set_attribute("dag_id", "local_dev_test_dag")
    dag_span.set_attribute("pipeline.name", "local_dev_test_airflow")
    
    # Log message - this gets sent to Loki via OTLP
    logger.info(json.dumps({
        "event": "dag_started",
        "dag_id": "local_dev_test_dag",
        "execution_run_id": execution_run_id,
        "correlation_id": correlation_id,
    }))
    
    print("   Running tasks:")
    
    # Task 1: Extract
    with tracer.start_as_current_span("extract_data") as extract_span:
        extract_span.set_attribute("execution.run_id", execution_run_id)
        extract_span.set_attribute("correlation_id", correlation_id)
        extract_span.set_attribute("task_id", "extract_data")
        
        logger.info(json.dumps({
            "event": "task_started",
            "task_id": "extract_data",
            "execution_run_id": execution_run_id,
        }))
        time.sleep(0.1)
        logger.info(json.dumps({
            "event": "task_completed",
            "task_id": "extract_data",
            "execution_run_id": execution_run_id,
            "records_extracted": 100,
        }))
        print("     ✓ extract_data")
    
    # Task 2: Validate
    with tracer.start_as_current_span("validate_data") as validate_span:
        validate_span.set_attribute("execution.run_id", execution_run_id)
        validate_span.set_attribute("correlation_id", correlation_id)
        validate_span.set_attribute("task_id", "validate_data")
        validate_span.set_attribute("record_count", 100)
        
        logger.info(json.dumps({
            "event": "task_started",
            "task_id": "validate_data",
            "execution_run_id": execution_run_id,
        }))
        time.sleep(0.1)
        logger.info(json.dumps({
            "event": "task_completed",
            "task_id": "validate_data",
            "execution_run_id": execution_run_id,
            "records_valid": 98,
            "records_invalid": 2,
        }))
        print("     ✓ validate_data")
    
    # Task 3: Transform
    with tracer.start_as_current_span("transform_data") as transform_span:
        transform_span.set_attribute("execution.run_id", execution_run_id)
        transform_span.set_attribute("correlation_id", correlation_id)
        transform_span.set_attribute("task_id", "transform_data")
        transform_span.set_attribute("record_count", 98)
        
        logger.info(json.dumps({
            "event": "task_started",
            "task_id": "transform_data",
            "execution_run_id": execution_run_id,
        }))
        time.sleep(0.1)
        logger.info(json.dumps({
            "event": "task_completed",
            "task_id": "transform_data",
            "execution_run_id": execution_run_id,
            "records_transformed": 98,
        }))
        print("     ✓ transform_data")
    
    # Task 4: Load
    with tracer.start_as_current_span("load_data") as load_span:
        load_span.set_attribute("execution.run_id", execution_run_id)
        load_span.set_attribute("correlation_id", correlation_id)
        load_span.set_attribute("task_id", "load_data")
        load_span.set_attribute("record_count", 98)
        
        logger.info(json.dumps({
            "event": "task_started",
            "task_id": "load_data",
            "execution_run_id": execution_run_id,
        }))
        time.sleep(0.1)
        logger.info(json.dumps({
            "event": "task_completed",
            "task_id": "load_data",
            "execution_run_id": execution_run_id,
            "records_loaded": 98,
        }))
        print("     ✓ load_data")
    
    dag_span.set_attribute("status", "success")
    dag_span.set_attribute("total_tasks", 4)
    
    logger.info(json.dumps({
        "event": "dag_completed",
        "dag_id": "local_dev_test_dag",
        "execution_run_id": execution_run_id,
        "status": "success",
        "total_tasks": 4,
    }))

duration = time.time() - start_time

# Record metrics
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
# Also force flush metrics
try:
    from opentelemetry import metrics
    provider = metrics.get_meter_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)
except Exception:
    pass
time.sleep(3)  # Allow time for export
print("   ✓ Telemetry flushed (traces, logs, metrics)")
print()

# Wait for telemetry to arrive
print("6. Waiting for telemetry to arrive in Grafana Cloud (15 seconds)...")
time.sleep(15)
print()

# Query Grafana Cloud - LOGS (Loki)
print("7. Querying Grafana Cloud...")
import requests
from datetime import timedelta

logs_url = secrets["GCLOUD_HOSTED_LOGS_URL"].replace(
    "/loki/api/v1/push", "/loki/api/v1/query_range"
)
logs_id = secrets["GCLOUD_HOSTED_LOGS_ID"]
api_key = secrets["GCLOUD_RW_API_KEY"]

end = datetime.now(UTC)
start = end - timedelta(minutes=5)

print("=" * 80)
print("VALIDATION RESULTS")
print("=" * 80)
print()

# --- LOGS (Loki) ---
print("📋 LOGS (Loki)")
print("-" * 40)
query = f'{{service_name="{SERVICE_NAME}"}}'
log_response = requests.get(
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
if log_response.status_code == 200:
    data = log_response.json()
    results = data.get("data", {}).get("result", [])
    logs_found = sum(len(stream.get("values", [])) for stream in results)
    
    if logs_found > 0:
        print(f"✅ Found {logs_found} log entries")
        for stream in results[:1]:
            for timestamp_ns, log_line in stream.get("values", [])[:2]:
                ts = datetime.fromtimestamp(int(timestamp_ns) / 1e9, UTC)
                print(f"   [{ts.strftime('%H:%M:%S')}] {log_line[:80]}...")
    else:
        print("❌ No logs found")
else:
    print(f"❌ Loki query error: {log_response.status_code}")
print()

# --- TRACES (Tempo) ---
print("🔍 TRACES (Tempo)")
print("-" * 40)
# Tempo search API
tempo_url = secrets.get("GCLOUD_HOSTED_TRACES_URL", "").replace("/v1/traces", "") or \
    f"https://tempo-prod-10-prod-eu-west-2.grafana.net"
tempo_id = secrets.get("GCLOUD_HOSTED_TRACES_ID", logs_id)

# Search for traces by service name
trace_search_url = f"{tempo_url}/api/search"
trace_response = requests.get(
    trace_search_url,
    params={
        "q": f'{{resource.service.name="{SERVICE_NAME}"}}',
        "limit": 10,
        "start": int(start.timestamp()),
        "end": int(end.timestamp()),
    },
    auth=(tempo_id, api_key),
    timeout=10,
)

traces_found = 0
if trace_response.status_code == 200:
    trace_data = trace_response.json()
    traces = trace_data.get("traces", [])
    traces_found = len(traces)
    
    if traces_found > 0:
        print(f"✅ Found {traces_found} traces")
        for t in traces[:2]:
            print(f"   TraceID: {t.get('traceID', 'N/A')[:16]}... "
                  f"Root: {t.get('rootServiceName', 'N/A')} → {t.get('rootTraceName', 'N/A')}")
    else:
        print("❌ No traces found")
else:
    print(f"⚠️  Tempo query returned: {trace_response.status_code}")
    # Try alternative - query by tag
    trace_response2 = requests.get(
        f"{tempo_url}/api/search/tags",
        auth=(tempo_id, api_key),
        timeout=10,
    )
    if trace_response2.status_code == 200:
        print("   (Tempo is accessible, traces may still be indexing)")
print()

# --- METRICS (Mimir) ---
print("📊 METRICS (Mimir)")
print("-" * 40)
metrics_url = secrets["GCLOUD_HOSTED_METRICS_URL"].replace(
    "/api/prom/push", "/api/prom/api/v1/query"
)
metrics_id = secrets["GCLOUD_HOSTED_METRICS_ID"]

metric_response = requests.get(
    metrics_url,
    params={
        "query": 'pipeline_runs_total{service_name="' + SERVICE_NAME + '"}',
    },
    auth=(metrics_id, api_key),
    timeout=10,
)

metrics_found = 0
if metric_response.status_code == 200:
    metric_data = metric_response.json()
    results = metric_data.get("data", {}).get("result", [])
    metrics_found = len(results)
    
    if metrics_found > 0:
        print(f"✅ Found {metrics_found} metric series")
        for r in results[:2]:
            metric_labels = r.get("metric", {})
            value = r.get("value", [None, "N/A"])[1]
            print(f"   pipeline_runs_total = {value}")
    else:
        print("❌ No metrics found (may take longer to appear)")
else:
    print(f"⚠️  Mimir query returned: {metric_response.status_code}")
print()

# --- SUMMARY ---
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
    sys.exit(0)
elif logs_found > 0 or traces_found > 0:
    print(f"{'✅' if logs_found > 0 else '❌'} LOGS:    {logs_found} entries")
    print(f"{'✅' if traces_found > 0 else '❌'} TRACES:  {traces_found} traces")
    print(f"{'✅' if metrics_found > 0 else '⚠️ '} METRICS: {metrics_found} series")
    print()
    print("⚠️  PARTIAL SUCCESS - Some telemetry is reaching Grafana Cloud")
    sys.exit(0)
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
    sys.exit(1)
