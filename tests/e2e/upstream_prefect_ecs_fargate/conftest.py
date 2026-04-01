"""Fixtures for Prefect ECS test case.

These tests require deployed AWS infrastructure and should be skipped in CI.
Run manually with: pytest tests/e2e/upstream_prefect_ecs_fargate/ -v
"""

import pytest

from tests.shared.infra import infrastructure_available


@pytest.fixture(scope="session")
def failure_data() -> dict:
    """Fixture for Prefect pipeline failure data - skip if infrastructure unavailable."""
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI - run manually")

    from tests.e2e.upstream_prefect_ecs_fargate.test_agent_e2e import (
        CONFIG,
        _get_run_and_trace_ids,
        get_failure_details_from_logs,
        trigger_pipeline_failure,
    )

    if not CONFIG.get("trigger_api_url"):
        pytest.skip("Infrastructure not deployed (trigger_api_url not configured)")
    if not CONFIG.get("log_group"):
        pytest.skip("Infrastructure not deployed (log_group not configured)")

    run_id, trace_id = _get_run_and_trace_ids()
    data = trigger_pipeline_failure(run_id, trace_id)
    if not data:
        pytest.skip("Could not trigger pipeline failure")

    return get_failure_details_from_logs(data, run_id, trace_id)
