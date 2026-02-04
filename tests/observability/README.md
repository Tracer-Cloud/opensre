# Observability Scripts

These scripts load environment variables from `.env` at the repo root.

## validate_grafana_cloud.py

Checks Loki, Mimir, and Tempo endpoint reachability with HTTP basic auth.

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
python3 tests/observability/validate_grafana_cloud.py
```

## run_local_with_cloud.py

Runs the local Prefect flow while exporting telemetry to Grafana Cloud.

Required environment variables:
- GCLOUD_OTLP_ENDPOINT
- GCLOUD_OTLP_AUTH_HEADER

Run:
```
python3 tests/observability/run_local_with_cloud.py
```
