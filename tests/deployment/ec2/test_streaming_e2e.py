"""End-to-end test: stream an investigation from a deployed EC2 agent.

Requires deployed infrastructure (see conftest.py / deploy.py).
Run with: pytest tests/deployment/ec2/test_streaming_e2e.py -v -s
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.cli.wizard.store import load_remote_url
from app.remote.client import RemoteAgentClient, normalize_url

logger = logging.getLogger(__name__)


@pytest.mark.e2e
class TestEC2Health:
    """Validate health check via the RemoteAgentClient."""

    def test_health_via_client(self, ec2_deployment: dict[str, Any]) -> None:
        """Connect using RemoteAgentClient and verify the health response."""
        ip = ec2_deployment["PublicIpAddress"]
        client = RemoteAgentClient(ip)

        data = client.health()
        assert data.get("ok") is not None, f"Unexpected health response: {data}"
        logger.info("Health via client OK: %s", data)


@pytest.mark.e2e
class TestEC2Streaming:
    """Validate live-streaming of investigation steps from a deployed EC2 agent."""

    def test_stream_investigation(self, ec2_deployment: dict[str, Any]) -> None:
        """Trigger a synthetic alert, consume the SSE stream, and validate events."""
        ip = ec2_deployment["PublicIpAddress"]
        client = RemoteAgentClient(ip)

        result = client.run_streamed_investigation()

        assert result.events_received > 0, "No streaming events received"
        assert result.saw_end is True, "Expected the streamed run to emit an end event"
        seen = result.node_names_seen
        logger.info("Nodes seen: %s", seen)
        assert "extract_alert" in seen, f"extract_alert not in streamed nodes: {seen}"
        assert any(
            node in seen for node in ("plan_actions", "investigate", "diagnose", "publish")
        ), f"Expected downstream investigation nodes, got {seen}"

        has_output = (
            result.final_state.get("root_cause")
            or result.final_state.get("report")
            or result.final_state.get("is_noise")
        )
        assert has_output, (
            f"Final state missing root_cause/report/is_noise: {list(result.final_state.keys())}"
        )

        logger.info(
            "Stream investigation OK: %d events, %d nodes, has_output=%s",
            result.events_received,
            len(seen),
            bool(has_output),
        )

    def test_trigger_incident(self, ec2_deployment: dict[str, Any]) -> None:
        """Trigger a synthetic incident and verify the stream produces events."""
        ip = ec2_deployment["PublicIpAddress"]
        client = RemoteAgentClient(ip)
        result = client.run_streamed_investigation()

        assert result.events_received > 0, "No events from triggered incident"
        assert result.saw_end is True, "Triggered incident did not finish streaming"
        assert len(result.node_names_seen) >= 1, (
            f"Expected node events, got: {result.node_names_seen}"
        )

        logger.info(
            "Trigger incident OK: %d events, nodes=%s",
            result.events_received,
            result.node_names_seen,
        )

    def test_saved_url_matches_deployment(self, ec2_deployment: dict[str, Any]) -> None:
        """The deploy step should persist the deployed remote URL to ~/.opensre."""
        ip = ec2_deployment["PublicIpAddress"]

        assert load_remote_url() == normalize_url(ip)

    def test_cli_trigger_uses_saved_url(self, ec2_deployment: dict[str, Any]) -> None:
        """The CLI should work without --url after deploy saved the remote URL."""
        _ = ec2_deployment
        runner = CliRunner()

        result = runner.invoke(cli, ["remote", "trigger"], env={"TRACER_OUTPUT_FORMAT": "text"})

        assert result.exit_code == 0, result.output
        assert "Remote Investigation" in result.output
