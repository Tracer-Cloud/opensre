"""Fixtures for Vercel deployment test case.

These tests require a VERCEL_API_TOKEN.
Run manually with: pytest tests/deployment/vercel/ -v -s
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest

from tests.shared.infra import infrastructure_available


def _vercel_available() -> bool:
    """Return True when Vercel credentials are available."""
    if not infrastructure_available():
        return False
    return bool(os.getenv("VERCEL_API_TOKEN"))


@pytest.fixture(scope="session")
def vercel_deployment() -> Generator[dict[str, Any], None, None]:
    """Deploy to Vercel, yield outputs, then tear down.

    Skips when running in CI, when SKIP_INFRA_TESTS is set, or when
    VERCEL_API_TOKEN is not configured.
    """
    if not _vercel_available():
        pytest.skip(
            "Vercel deployment tests skipped — set VERCEL_API_TOKEN "
            "(get one from https://vercel.com/account/tokens)"
        )

    from tests.deployment.vercel.infrastructure_sdk.client import VercelPermissionError
    from tests.deployment.vercel.infrastructure_sdk.deploy import deploy
    from tests.deployment.vercel.infrastructure_sdk.destroy import destroy

    try:
        outputs = deploy()
    except VercelPermissionError as exc:
        pytest.skip(
            f"Vercel token lacks required permissions: {exc}. "
            "Create a token with Read/Write access to Projects and Deployments."
        )
        return  # unreachable but satisfies type checker

    try:
        yield outputs
    finally:
        destroy()
