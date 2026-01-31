"""Fixtures for upstream Lambda test case.

These tests require deployed AWS infrastructure and should be skipped in CI.
Run manually with: pytest tests/test_case_upstream_lambda/ -v
"""

import os

import pytest


def _infrastructure_available() -> bool:
    """Check if AWS infrastructure is available for testing."""
    # Skip if running in CI or if explicitly disabled
    return not (os.getenv("CI") or os.getenv("SKIP_INFRA_TESTS"))


@pytest.fixture
def stack_outputs() -> dict:
    """Fixture for CDK stack outputs - skip if infrastructure unavailable."""
    if not _infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI - run manually")
    # Return placeholder - actual values come from CDK deployment
    return {}


@pytest.fixture
def failure_data() -> dict:
    """Fixture for pipeline failure data - skip if infrastructure unavailable."""
    if not _infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI - run manually")
    # Return placeholder - actual values come from trigger_pipeline_failure()
    return {}
