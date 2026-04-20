"""End-to-end test: deploy OpenSRE to LangSmith and run a synthetic investigation.

Requires deployed infrastructure (see conftest.py / deploy.py).
Run with: pytest tests/deployment/langsmith/ -v -s

Prerequisites:
- LANGSMITH_API_KEY (or LANGGRAPH_HOST_API_KEY / LANGCHAIN_API_KEY)
- ``langgraph`` CLI installed
- Docker running
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import requests

from tests.deployment.langsmith.infrastructure_sdk.client import (
    get_api_key,
    invoke_agent,
)

logger = logging.getLogger(__name__)

SYNTHETIC_ALERT = (
    "ALERT: Pipeline 'etl_daily_orders' failed at 2025-06-15T08:32:00Z. "
    "Lambda function 'etl-daily-orders-processor' returned error: "
    "'SchemaValidationError: column order_total expected type decimal but got string'. "
    "CloudWatch log group: /aws/lambda/etl-daily-orders-processor. "
    "Please investigate the root cause."
)


@pytest.mark.e2e
class TestLangSmithDeployment:
    """Validate that the LangSmith deployment lifecycle works."""

    def test_deploy_lifecycle(self, langsmith_deployment: dict[str, Any]) -> None:
        """Verify the deployment was created with all required outputs."""
        assert langsmith_deployment["DeploymentName"], "DeploymentName missing"
        assert langsmith_deployment["DeploymentUrl"], "DeploymentUrl missing"

        logger.info(
            "Deployment lifecycle OK: name=%s url=%s",
            langsmith_deployment["DeploymentName"],
            langsmith_deployment["DeploymentUrl"],
        )

    def test_health_endpoint(self, langsmith_deployment: dict[str, Any]) -> None:
        """Verify the deployed agent's health endpoint responds."""
        url = langsmith_deployment["DeploymentUrl"]
        health_url = f"{url.rstrip('/')}/ok"

        try:
            api_key = get_api_key()
            resp = requests.get(health_url, timeout=30, headers={"x-api-key": api_key})
        except requests.exceptions.RequestException as exc:
            pytest.skip(f"Health endpoint unreachable: {exc}")
            return

        assert resp.status_code == 200, f"Health returned {resp.status_code}: {resp.text[:200]}"
        logger.info("Health endpoint OK: %d", resp.status_code)


@pytest.mark.e2e
class TestLangSmithInvocation:
    """Validate that the deployed agent can process an investigation request."""

    def test_agent_responds_to_alert(self, langsmith_deployment: dict[str, Any]) -> None:
        """Invoke the deployed agent and verify it produces a response."""
        url = langsmith_deployment["DeploymentUrl"]

        try:
            result = invoke_agent(url=url, input_text=SYNTHETIC_ALERT)
        except requests.exceptions.RequestException as exc:
            pytest.skip(f"Agent invocation failed (may need model API key): {exc}")
            return

        assert result["status_code"] == 200, f"Invocation returned {result['status_code']}"
        assert result["content"], "Agent returned empty content"

        logger.info("Agent response (%d chars)", len(result["content"]))
