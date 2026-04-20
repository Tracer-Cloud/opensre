"""End-to-end test: provision a Bedrock Agent and run a synthetic investigation.

Requires deployed infrastructure (see conftest.py / deploy.py).
Run with: pytest tests/deployment/bedrock/ -v -s

NOTE: Invocation tests require that model access is enabled for the configured
foundation model in the Bedrock console. Without model access, only the
deployment lifecycle test will pass.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from botocore.exceptions import ClientError, EventStreamError, ReadTimeoutError

from tests.deployment.bedrock.infrastructure_sdk.agent import (
    get_bedrock_tools,
    invoke_agent_with_tool_loop,
)

logger = logging.getLogger(__name__)

SYNTHETIC_ALERT = (
    "ALERT: Pipeline 'etl_daily_orders' failed at 2025-06-15T08:32:00Z. "
    "Lambda function 'etl-daily-orders-processor' returned error: "
    "'SchemaValidationError: column order_total expected type decimal but got string'. "
    "The failure originated in the data transformation step after ingesting data "
    "from S3 bucket 'acme-landing-prod' prefix 'raw/orders/2025-06-15/'. "
    "CloudWatch log group: /aws/lambda/etl-daily-orders-processor. "
    "Correlation ID: corr-8f3a-4b2c. "
    "Please investigate the root cause."
)

_MODEL_ACCESS_ERRORS = (
    "resourceNotFoundException",
    "dependencyFailedException",
    "doesn't support tool use",
)


def _is_model_access_error(exc: Exception) -> bool:
    """Return True if the exception indicates the model isn't accessible for agent invocation."""
    msg = str(exc).lower()
    return any(e.lower() in msg for e in _MODEL_ACCESS_ERRORS)


@pytest.mark.e2e
class TestBedrockAgentDeployment:
    """Validate that the Bedrock Agent deployment lifecycle works."""

    def test_deploy_lifecycle(self, bedrock_agent: dict[str, Any]) -> None:
        """Verify the agent was provisioned with all required resources."""
        assert bedrock_agent["AgentId"], "AgentId missing from deployment outputs"
        assert bedrock_agent["AgentAliasId"], "AgentAliasId missing"
        assert bedrock_agent["RoleName"], "RoleName missing"
        assert bedrock_agent["RoleArn"], "RoleArn missing"
        assert bedrock_agent["ActionGroupId"], "ActionGroupId missing"

        logger.info(
            "Deployment lifecycle OK: agent=%s alias=%s",
            bedrock_agent["AgentId"],
            bedrock_agent["AgentAliasId"],
        )


@pytest.mark.e2e
class TestBedrockAgentInvestigation:
    """Validate that a Bedrock Agent can orchestrate OpenSRE tools.

    These tests require model access to be enabled in the Bedrock console for the
    configured foundation model. If model access isn't available, tests are skipped
    with a descriptive message.
    """

    def test_agent_responds_to_alert(self, bedrock_agent: dict[str, Any]) -> None:
        """Invoke the agent with a synthetic alert and verify it produces a response."""
        agent_id = bedrock_agent["AgentId"]
        alias_id = bedrock_agent["AgentAliasId"]

        tools = get_bedrock_tools()
        tool_map = {t.name: t for t in tools}

        logger.info("Invoking Bedrock Agent %s with synthetic alert", agent_id)

        try:
            response = invoke_agent_with_tool_loop(
                agent_id=agent_id,
                alias_id=alias_id,
                input_text=SYNTHETIC_ALERT,
                tool_map=tool_map,
            )
        except (EventStreamError, ClientError, ReadTimeoutError) as exc:
            if _is_model_access_error(exc):
                pytest.skip(
                    f"Model access not available for agent invocation. "
                    f"Enable model access in the Bedrock console. Error: {exc}"
                )
            raise

        assert response, "Agent returned an empty response"
        assert len(response) > 50, f"Response too short ({len(response)} chars): {response[:200]}"

        logger.info("Agent response (%d chars): %s", len(response), response[:500])

    def test_agent_uses_tools(self, bedrock_agent: dict[str, Any]) -> None:
        """Verify the agent attempts to call at least one tool via RETURN_CONTROL."""
        agent_id = bedrock_agent["AgentId"]
        alias_id = bedrock_agent["AgentAliasId"]

        tools = get_bedrock_tools()
        tool_map = {t.name: t for t in tools}

        invoked_tools: list[str] = []
        original_runs: dict[str, Any] = {}

        for name, tool in tool_map.items():
            original_runs[name] = tool.run

            def _tracking_run(_name: str = name, _orig: Any = tool.run) -> Any:
                def wrapper(**kwargs: Any) -> Any:
                    invoked_tools.append(_name)
                    return _orig(**kwargs)

                return wrapper

            tool.run = _tracking_run()  # type: ignore[assignment]

        try:
            invoke_agent_with_tool_loop(
                agent_id=agent_id,
                alias_id=alias_id,
                input_text=SYNTHETIC_ALERT,
                tool_map=tool_map,
            )
        except (EventStreamError, ClientError, ReadTimeoutError) as exc:
            if _is_model_access_error(exc):
                pytest.skip(
                    f"Model access not available for agent invocation. "
                    f"Enable model access in the Bedrock console. Error: {exc}"
                )
            raise
        finally:
            for name, orig in original_runs.items():
                tool_map[name].run = orig

        assert invoked_tools, (
            "Agent did not invoke any tools. Expected at least one RETURN_CONTROL cycle."
        )
        logger.info("Agent invoked tools: %s", invoked_tools)
