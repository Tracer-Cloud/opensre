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


def test_deploy_langsmith_success_prints_url() -> None:
    runner = CliRunner()

    with (
        patch(
            "app.cli.commands.deploy.is_langgraph_cli_installed",
            return_value=(True, "langgraph CLI is installed."),
        ),
        patch(
            "app.cli.commands.deploy.resolve_langsmith_api_key",
            return_value="sk-test",
        ),
        patch(
            "app.cli.commands.deploy.validate_langsmith_api_key",
            return_value=(True, "LangSmith API key validated."),
        ),
        patch(
            "app.cli.commands.deploy.resolve_deployment_name",
            return_value="open-sre-agent",
        ),
        patch("app.cli.commands.deploy.persist_langsmith_env"),
        patch(
            "app.cli.commands.deploy.run_langsmith_deploy",
            return_value=(0, "Deployment complete\nhttps://example.langsmith.com"),
        ),
        patch(
            "app.cli.commands.deploy.extract_deployment_url",
            return_value="https://example.langsmith.com",
        ),
    ):
        result = runner.invoke(cli, ["deploy", "langsmith"])

    assert result.exit_code == 0
    assert "Deploying to LangSmith..." in result.output
    assert "Deployment URL: https://example.langsmith.com" in result.output


def test_deploy_langsmith_invalid_key_fails() -> None:
    runner = CliRunner()

    with (
        patch(
            "app.cli.commands.deploy.is_langgraph_cli_installed",
            return_value=(True, "langgraph CLI is installed."),
        ),
        patch(
            "app.cli.commands.deploy.resolve_langsmith_api_key",
            return_value="sk-bad",
        ),
        patch(
            "app.cli.commands.deploy.validate_langsmith_api_key",
            return_value=(False, "Invalid LangSmith API key."),
        ),
    ):
        result = runner.invoke(cli, ["deploy", "langsmith"])

    assert result.exit_code != 0
    assert "Invalid LangSmith API key." in result.output


def test_deploy_langsmith_build_only_passes_flag() -> None:
    runner = CliRunner()

    with (
        patch(
            "app.cli.commands.deploy.is_langgraph_cli_installed",
            return_value=(True, "langgraph CLI is installed."),
        ),
        patch(
            "app.cli.commands.deploy.resolve_langsmith_api_key",
            return_value="sk-test",
        ),
        patch(
            "app.cli.commands.deploy.validate_langsmith_api_key",
            return_value=(True, "LangSmith API key validated."),
        ),
        patch(
            "app.cli.commands.deploy.resolve_deployment_name",
            return_value="open-sre-agent",
        ),
        patch("app.cli.commands.deploy.persist_langsmith_env"),
        patch(
            "app.cli.commands.deploy.run_langsmith_deploy",
            return_value=(0, "Build complete"),
        ) as mock_run,
        patch(
            "app.cli.commands.deploy.extract_deployment_url",
            return_value=None,
        ),
    ):
        result = runner.invoke(
            cli,
            ["deploy", "langsmith", "--build-only", "--api-key", "sk-test"],
        )

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        api_key="sk-test",
        deployment_name="open-sre-agent",
        build_only=True,
    )


def test_deploy_langsmith_missing_key_fails() -> None:
    runner = CliRunner()

    with (
        patch(
            "app.cli.commands.deploy.is_langgraph_cli_installed",
            return_value=(True, "langgraph CLI is installed."),
        ),
        patch(
            "app.cli.commands.deploy.resolve_langsmith_api_key",
            return_value=None,
        ),
    ):
        result = runner.invoke(cli, ["deploy", "langsmith"])

    assert result.exit_code != 0
    assert "LangSmith API key not found." in result.output
