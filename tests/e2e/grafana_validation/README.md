# Grafana Cloud Validation

## validate_grafana_cloud.py

Checks Grafana Cloud ingestion for `prefect-etl-pipeline` using logs, metrics, and traces.

Required environment variables:
- GCLOUD_HOSTED_METRICS_URL
- GCLOUD_HOSTED_METRICS_ID
- GCLOUD_HOSTED_LOGS_URL
- GCLOUD_HOSTED_LOGS_ID
- GCLOUD_HOSTED_TRACES_URL_TEMPO
- GCLOUD_HOSTED_TRACES_ID
- GCLOUD_RW_API_KEY

Run:
```
python3 tests/e2e/grafana_validation/validate_grafana_cloud.py
```

## grafana_otlp_env_check.py

Ensures OTLP env vars are present and prints next steps for a local Prefect run.

Required environment variables:
- GCLOUD_OTLP_ENDPOINT
- GCLOUD_OTLP_AUTH_HEADER

Run:
```
python3 tests/e2e/grafana_validation/grafana_otlp_env_check.py
```

## Pytest Query Smoke Tests

These tests verify that Grafana Cloud query endpoints respond successfully
without requiring pipeline-specific telemetry.

Prerequisites:
- `GRAFANA_READ_TOKEN` available in `.env` or environment
- Optional: `GRAFANA_INSTANCE_URL` (defaults to `https://tracerbio.grafana.net`)
 
Note: These tests skip when configuration is missing, and they also skip when the configured live Grafana credentials are rejected with auth errors such as `401` or `403`.

Run:
```
python3 -m pytest tests/e2e/grafana_validation/test_grafana_cloud_queries.py -v
```
