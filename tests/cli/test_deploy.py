from __future__ import annotations

from unittest.mock import patch

from click import ClickException
from click.testing import CliRunner

from app.cli.__main__ import cli


def test_deploy_ec2_health_check_failure_is_non_fatal() -> None:
    runner = CliRunner()
    outputs = {"PublicIpAddress": "10.0.0.1", "ServerPort": "2024"}

    with (
        patch("tests.deployment.ec2.infrastructure_sdk.deploy_remote.deploy", return_value=outputs),
        patch("app.cli.commands.deploy._persist_remote_url"),
        patch(
            "app.cli.commands.remote_health.run_remote_health_check",
            side_effect=ClickException("Connection timed out"),
        ),
    ):
        result = runner.invoke(cli, ["deploy", "ec2"])

    assert result.exit_code == 0
    assert "[warn] Health check: Connection timed out" in result.output
    assert "Deployment provisioned. Retry with: opensre remote health" in result.output
