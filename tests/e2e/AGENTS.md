# Test Specification Principles

Principles for `tests/e2e/` live end-to-end tests. Real fixtures in this
directory are the canonical usage examples — this file states the rules, not
the code.

## 1. Real end-to-end testing: no mocking

Tests must exercise real services and real infrastructure (live installers,
Grafana Cloud, PostHog, incident.io, Docker builds) — no mocked services or
simulated responses. Mocking here would validate the agent against artificial
payloads instead of what production actually produces.

## 2. Separation of concerns: pure business logic

Any workload code a test drives must be isolated from test
orchestration/observability code — it should look like real customer code so
tests validate behavior against production-like systems rather than
instrumented ones. Anti-pattern: mixing test infrastructure into business
logic.

## 3. Gate on environment, skip loudly

Live suites declare their required credentials/environment up front (see
`grafana_validation/env_requirements.py`) and skip with a clear reason when
they are absent, so CI without secrets stays green without hiding failures
from runs that do have credentials.
