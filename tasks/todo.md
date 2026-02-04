## Grafana Cloud Validation Move

- [x] Move observability scripts into test_case_grafana_validation
- [x] Add GrafanaCloud class and pytest smoke tests
- [x] Remove Prefect execution from run_local_with_cloud
- [x] Update docs and cleanup ignores

## Results

- Added `GrafanaCloud` class + pytest smoke tests for prefect-etl-pipeline ingestion.
- Moved scripts to `tests/test_case_grafana_validation/` and removed `tests/observability/`.
- Updated README and `.dockerignore` to reflect the new location.
