"""Fixtures for EC2 deployment test case.

These tests require AWS credentials with EC2 access and should be skipped in CI.
Run manually with: pytest tests/deployment/ec2/ -v -s
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from tests.shared.infra import infrastructure_available


@pytest.fixture(scope="session")
def ec2_deployment() -> Generator[dict[str, Any]]:
    """Deploy OpenSRE on EC2, yield outputs, then terminate.

    Skips when running in CI or when SKIP_INFRA_TESTS is set.
    """
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI — run manually")

    from tests.deployment.ec2.infrastructure_sdk.deploy import deploy
    from tests.deployment.ec2.infrastructure_sdk.destroy import destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()
