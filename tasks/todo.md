## Observability Test Cleanup

- [x] Delete redundant observability scripts/tests
- [x] Simplify validate_grafana_cloud.py
- [x] Simplify run_local_with_cloud.py
- [x] Add observability README
- [x] Document results

## Results

- Reduced to two scripts + README under `tests/observability/`.
- `validate_grafana_cloud.py` now performs parallel endpoint checks with 2s timeouts.
- `run_local_with_cloud.py` remains env-only with inline `.env` loading.
