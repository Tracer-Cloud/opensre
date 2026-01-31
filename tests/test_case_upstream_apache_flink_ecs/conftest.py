"""Fixtures for Flink ECS test case.

These tests require deployed AWS infrastructure and should be skipped in CI.
Run manually with: pytest tests/test_case_upstream_apache_flink_ecs/ -v
"""

import os

import pytest


def _infrastructure_available() -> bool:
    """Check if AWS infrastructure is available for testing."""
    # Skip if running in CI or if explicitly disabled
    return not (os.getenv("CI") or os.getenv("SKIP_INFRA_TESTS"))


@pytest.fixture(scope="session")
def failure_data() -> dict:
    """Fixture for Flink pipeline failure data - skip if infrastructure unavailable."""
    if not _infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI - run manually")

    from tests.test_case_upstream_apache_flink_ecs.test_agent_e2e import (
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
