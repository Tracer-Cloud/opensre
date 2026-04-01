"""Fixtures for Flink ECS test case.

These tests require deployed AWS infrastructure and should be skipped in CI.
Run manually with: pytest tests/e2e/upstream_apache_flink_ecs/ -v
"""

import pytest

from tests.shared.infra import infrastructure_available


@pytest.fixture(scope="session")
def failure_data() -> dict:
    """Fixture for Flink pipeline failure data - skip if infrastructure unavailable."""
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI - run manually")

    from tests.e2e.upstream_apache_flink_ecs.test_agent_e2e import (
        CONFIG,
        get_failure_details,
        trigger_pipeline_failure,
    )

    # Check if CONFIG has required values
    if not CONFIG.get("trigger_api_url"):
        pytest.skip("Infrastructure not deployed (trigger_api_url not configured)")

    data = trigger_pipeline_failure()
    if not data:
        pytest.skip("Could not trigger pipeline failure")

    return get_failure_details(data)
