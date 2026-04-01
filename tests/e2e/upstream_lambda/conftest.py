"""Fixtures for upstream Lambda test case.

These tests require deployed AWS infrastructure and should be skipped in CI.
Run manually with: pytest tests/e2e/upstream_lambda/ -v
"""

import pytest

from tests.shared.infra import infrastructure_available


@pytest.fixture
def stack_outputs() -> dict:
    """Fixture for CDK stack outputs - skip if infrastructure unavailable."""
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI - run manually")
    # Return placeholder - actual values come from CDK deployment
    return {}


@pytest.fixture(scope="session")
def failure_data() -> dict:
    """Fixture for pipeline failure data - skip if infrastructure unavailable."""
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI - run manually")
    from tests.e2e.upstream_lambda.test_agent_e2e import trigger_pipeline_failure

    return trigger_pipeline_failure()
