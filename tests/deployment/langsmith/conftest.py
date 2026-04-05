"""Fixtures for LangSmith deployment test case.

These tests require a LANGSMITH_API_KEY, the ``langgraph`` CLI, and Docker.
Run manually with: pytest tests/deployment/langsmith/ -v -s
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest

from tests.shared.infra import infrastructure_available


def _langsmith_available() -> bool:
    """Return True when LangSmith credentials and tools are available."""
    if not infrastructure_available():
        return False
    return bool(
        os.getenv("LANGSMITH_API_KEY")
        or os.getenv("LANGGRAPH_HOST_API_KEY")
        or os.getenv("LANGCHAIN_API_KEY")
    )


@pytest.fixture(scope="session")
def langsmith_deployment() -> Generator[dict[str, Any], None, None]:
    """Deploy to LangSmith, yield outputs, then clean up.

    Skips when running in CI, when SKIP_INFRA_TESTS is set, or when
    LangSmith API key is not configured.
    """
    if not _langsmith_available():
        pytest.skip(
            "LangSmith deployment tests skipped — set LANGSMITH_API_KEY "
            "and ensure langgraph CLI + Docker are available"
        )

    from tests.deployment.langsmith.infrastructure_sdk.deploy import deploy
    from tests.deployment.langsmith.infrastructure_sdk.destroy import destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()
