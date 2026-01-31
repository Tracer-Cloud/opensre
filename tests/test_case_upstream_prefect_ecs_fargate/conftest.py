"""Fixtures for Prefect ECS test case.

These tests require deployed AWS infrastructure and should be skipped in CI.
Run manually with: pytest tests/test_case_upstream_prefect_ecs_fargate/ -v
"""

import os

import pytest


def _infrastructure_available() -> bool:
    """Check if AWS infrastructure is available for testing."""
    # Skip if running in CI or if explicitly disabled
    return not (os.getenv("CI") or os.getenv("SKIP_INFRA_TESTS"))


@pytest.fixture(scope="session")
def failure_data() -> dict:
    """Fixture for Prefect pipeline failure data - skip if infrastructure unavailable."""
    if not _infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI - run manually")
    from tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e import (
        get_failure_details,
    )

    return get_failure_details()
