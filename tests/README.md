# Tests Catalog Naming Conventions

This document defines semantic naming for the test catalog so test type and environment boundaries are obvious at a glance.

## Top-level taxonomy

- `tests/synthetic/`: synthetic RCA simulations with scored fixtures and deterministic scenario assets.
- `tests/e2e/`: real end-to-end scenarios that execute against real services and infrastructure.
- `tests/deployment/`: deployment validation and infrastructure deployment tests.
- `tests/<domain>/`: unit and integration tests for product modules (for example `cli/`, `tools/`, `integrations/`, `services/`).

## E2E naming rules

- Directory name format: `tests/e2e/<scenario_name>/` where `<scenario_name>` describes system and workload (example: `upstream_lambda`, `kubernetes`).
- Environment-specific test files use explicit filenames:
  - `test_local.py` for local environments.
  - `test_<cloud>.py` for cloud environments (example: `test_eks.py`).
- Use explicit environment-oriented filenames when possible: `test_local.py`, `test_aws.py`, or `test_cloud.py`.

## Synthetic naming rules

- Scenario suite path format: `tests/synthetic/<domain>/<scenario_id>-<slug>/`.
- Scenario ids are numeric and ordered (example: `001-replication-lag`).
- Shared synthetic utilities stay under `tests/synthetic/<domain>/shared/`.

## Telemetry naming rules

- `OTEL_RESOURCE_ATTRIBUTES` values must use semantic catalog names and must not use legacy `test_case_*` values.
- Use `test_case=e2e_<scenario_name>` for e2e scenarios.
- Use `test_case=synthetic_<suite_or_scenario_name>` for synthetic suites when applicable.

## Legacy names

Legacy `test_case_*` path naming under `tests/` is deprecated. Use `tests/e2e/*` and `tests/synthetic/*` only.
