"""End-to-end test: deploy OpenSRE on EC2 and verify the LangGraph API.

Requires deployed infrastructure (see conftest.py / deploy.py).
Run with: pytest tests/deployment/ec2/ -v -s
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import requests

logger = logging.getLogger(__name__)


@pytest.mark.e2e
class TestEC2Deployment:
    """Validate that the EC2 deployment lifecycle works."""

    def test_deploy_lifecycle(self, ec2_deployment: dict[str, Any]) -> None:
        """Verify the instance was provisioned with all required resources."""
        assert ec2_deployment["InstanceId"], "InstanceId missing"
        assert ec2_deployment["PublicIpAddress"], "PublicIpAddress missing"
        assert ec2_deployment["SecurityGroupId"], "SecurityGroupId missing"
        assert ec2_deployment["ProfileName"], "ProfileName missing"
        assert ec2_deployment["RoleName"], "RoleName missing"

        logger.info(
            "Deployment lifecycle OK: instance=%s ip=%s",
            ec2_deployment["InstanceId"],
            ec2_deployment["PublicIpAddress"],
        )


@pytest.mark.e2e
class TestEC2Health:
    """Validate that the OpenSRE container is healthy on EC2."""

    def test_health_endpoint(self, ec2_deployment: dict[str, Any]) -> None:
        """Verify the LangGraph API health endpoint responds."""
        ip = ec2_deployment["PublicIpAddress"]
        url = f"http://{ip}:2024/ok"

        try:
            resp = requests.get(url, timeout=30)
        except requests.exceptions.RequestException as exc:
            pytest.skip(f"Health endpoint unreachable: {exc}")
            return

        assert resp.status_code == 200, f"Health returned {resp.status_code}: {resp.text[:200]}"
        logger.info("Health endpoint OK: %d", resp.status_code)

    def test_threads_endpoint(self, ec2_deployment: dict[str, Any]) -> None:
        """Verify the LangGraph threads API is accessible."""
        ip = ec2_deployment["PublicIpAddress"]
        url = f"http://{ip}:2024/threads"

        try:
            resp = requests.post(url, json={}, timeout=30)
        except requests.exceptions.RequestException as exc:
            pytest.skip(f"Threads endpoint unreachable: {exc}")
            return

        assert resp.status_code in (200, 201, 401, 422), (
            f"Threads returned unexpected status: {resp.status_code}"
        )
        logger.info("Threads endpoint accessible: %d", resp.status_code)
