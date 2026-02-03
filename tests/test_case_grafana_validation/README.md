# Grafana Cloud Validation Test Case

## Purpose

This test case validates the complete telemetry pipeline:
1. Run Prefect flow locally with OTLP → Grafana Cloud
2. Send metrics, traces, and logs to Grafana Cloud
3. Query Grafana Cloud APIs to retrieve telemetry
4. Validate that all pipeline spans are present with `execution.run_id`

## Prerequisites

- Python 3.11+ with prefect, boto3, requests installed
- AWS credentials configured
- Grafana Cloud read token in `.env`
- S3 buckets from Lambda test case

## Usage

### Run Test
```bash
cd tests/test_case_grafana_validation
python3 test_grafana_roundtrip.py
```

This will:
1. Configure OTLP to send to Grafana Cloud
2. Run Prefect flow locally (extract → validate → transform → load)
3. Flush telemetry to Grafana Cloud
4. Wait for ingestion (60 seconds)
5. Query Grafana Loki for logs with `execution_run_id`
6. Query Grafana Tempo for traces with pipeline spans
7. Query Grafana Mimir for metrics
8. Print comprehensive validation report

### Expected Output
```
✓ Telemetry sent to Grafana Cloud
✓ Logs found: 17 entries with execution_run_id
✓ Traces found: 1 trace with 4 pipeline spans
  - extract_data ✓
  - validate_data ✓
  - transform_data ✓
  - load_data ✓
✓ Metrics found: pipeline_runs_total, duration_seconds
```

## What This Tests

### Telemetry Export (OTLP)
- OpenTelemetry SDK configured for Grafana Cloud
- Logs exported via OTLP logs endpoint
- Traces exported via OTLP traces endpoint
- Metrics exported via OTLP metrics endpoint

### Grafana Cloud Ingestion
- Loki receives logs with proper labels
- Tempo receives traces with span attributes
- Mimir receives metrics with labels

### Query API
- LogQL queries work with `service_name` and `execution_run_id`
- Tempo trace search by service name
- PromQL queries for pipeline metrics

### Pipeline Instrumentation
- All 4 stages instrumented: extract, validate, transform, load
- `execution.run_id` in all spans
- `execution_run_id` in all logs
- Structured JSON logs queryable

## Architecture

```
Local Prefect Flow
  ↓ (OTLP over gRPC)
Grafana Cloud OTLP Gateway
  ↓
  ├─→ Loki (logs)
  ├─→ Tempo (traces)
  └─→ Mimir (metrics)
  ↓
Grafana Query API
  ↓
Test Validation Script
```

## Files

- `test_grafana_roundtrip.py` - Main test script
- `README.md` - This file
- Reuses: `../test_case_upstream_prefect_ecs_fargate/pipeline_code/prefect_flow/flow.py`
- Reuses: `../shared/telemetry/tracer_telemetry/` - Telemetry library
