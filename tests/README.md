# Tests

## Quick-start commands

| Goal | Command | When to use it |
|---|---|---|
| Run the default unit suite with coverage | `make test-cov` | First thing to run locally; no live infrastructure required. |
| Verify all integration configs and clients | `make verify-integrations` | After adding or changing an integration. |
| Run the default pytest collection (`tests/e2e` excluded by pytest configuration) | `make test-full` | Broad local or CI regression run. |

## Layout

Keep tests under domain directories — not loose files at the `tests/` root.

| Path | What it covers |
|---|---|
| `tests/<domain>/` | Unit and integration tests for product modules (`cli/`, `tools/`, `integrations/`, `core/`, `infrastructure/`, …). |
| `tests/e2e/` | Real end-to-end scenarios against live services and infrastructure. See [e2e/AGENTS.md](e2e/AGENTS.md) for scenario design principles. |
| `tests/github_ci/` | Repo hygiene guards (naming, import boundaries, architecture references). |
| `tests/conftest.py` | Shared pytest fixtures for the whole tree. |

## E2E naming rules

- Directory format: `tests/e2e/<scenario_name>/` where `<scenario_name>` describes system and workload (example: `install`, `quickstart`).
- Environment-specific test files use explicit filenames:
  - `test_local.py` for local environments.
  - `test_<cloud>.py` for cloud environments.

## Telemetry naming rules

- `OTEL_RESOURCE_ATTRIBUTES` values must use semantic catalog names and must not use legacy `test_case_*` values.
- Use `test_case=e2e_<scenario_name>` for e2e scenarios.

## Legacy names

Legacy `test_case_*` path naming under `tests/` is deprecated. Use `tests/e2e/*` only.
