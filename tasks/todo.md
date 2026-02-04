## Telemetry Re-export Fix (2026-02-04)

- [x] Audit tracer_telemetry re-exports after rename
- [x] Update tracer_telemetry modules to outbound_telemetry
- [x] Verify pytest collection for affected cases

## Results - Telemetry Re-export Fix (2026-02-04)

- Updated tracer_telemetry re-exports to outbound_telemetry.
- Ran `python3 -m pytest -q --collect-only tests/test_case_cloudwatch_demo/test_orchestrator.py tests/test_case_s3_failed_python_on_linux/test_orchestrator.py tests/test_case_superfluid/test_orchestrator.py` (no tests collected, imports clean).

## Grafana Cloud Validation Move

- [x] Move observability scripts into test_case_grafana_validation
- [x] Add GrafanaCloud class and pytest smoke tests
- [x] Remove Prefect execution from run_local_with_cloud
- [x] Update docs and cleanup ignores

## Results

- Added `GrafanaCloud` class + pytest smoke tests for prefect-etl-pipeline ingestion.
- Moved scripts to `tests/test_case_grafana_validation/` and removed `tests/observability/`.
- Updated README and `.dockerignore` to reflect the new location.
