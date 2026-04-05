"""End-to-end test: deploy a health-check function to Vercel.

Requires a VERCEL_API_TOKEN (see conftest.py / deploy.py).
Run with: pytest tests/deployment/vercel/ -v -s
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

logger = logging.getLogger(__name__)


@pytest.mark.e2e
class TestVercelDeployment:
    """Validate that the Vercel deployment lifecycle works."""

    def test_deploy_lifecycle(self, vercel_deployment: dict[str, Any]) -> None:
        """Verify the deployment was created with all required outputs."""
        assert vercel_deployment["DeploymentId"], "DeploymentId missing"
        assert vercel_deployment["DeploymentUrl"], "DeploymentUrl missing"
        assert vercel_deployment["ProjectName"], "ProjectName missing"

        logger.info(
            "Deployment lifecycle OK: id=%s url=%s",
            vercel_deployment["DeploymentId"],
            vercel_deployment["DeploymentUrl"],
        )

    def test_health_endpoint(self, vercel_deployment: dict[str, Any]) -> None:
        """Verify the deployed serverless function responds correctly."""
        url = vercel_deployment["DeploymentUrl"]
        health_url = f"{url.rstrip('/')}/api/health"

        with httpx.Client(timeout=30) as client:
            resp = client.get(health_url)

        assert resp.status_code == 200, f"Health returned {resp.status_code}: {resp.text[:200]}"

        body = json.loads(resp.text)
        assert body["status"] == "ok", f"Unexpected status: {body}"
        assert body["service"] == "opensre", f"Unexpected service: {body}"

        logger.info("Health endpoint OK: %s", body)

    def test_deployment_metadata(self, vercel_deployment: dict[str, Any]) -> None:
        """Verify deployment URL is a valid HTTPS endpoint."""
        url = vercel_deployment["DeploymentUrl"]
        assert url.startswith("https://"), f"Expected HTTPS URL, got: {url}"

        with httpx.Client(timeout=30) as client:
            resp = client.get(url)

        assert resp.status_code in (200, 404), (
            f"Deployment root returned unexpected status: {resp.status_code}"
        )

        logger.info("Deployment metadata OK: url=%s", url)
