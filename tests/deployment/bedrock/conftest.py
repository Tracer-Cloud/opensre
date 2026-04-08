"""Fixtures for Bedrock Agent deployment test case.

These tests require AWS credentials with Bedrock access and should be skipped in CI.
Run manually with: pytest tests/deployment/bedrock/ -v -s
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from tests.shared.infra import infrastructure_available


@pytest.fixture(scope="session")
def bedrock_agent() -> Generator[dict[str, Any], None, None]:
    """Deploy a Bedrock Agent, yield outputs, then tear down.

    Skips when running in CI or when SKIP_INFRA_TESTS is set.
    """
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI — run manually")

    from tests.deployment.bedrock.infrastructure_sdk.deploy import deploy
    from tests.deployment.bedrock.infrastructure_sdk.destroy import destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()
