from unittest.mock import patch

from click.testing import CliRunner

from surfaces.cli.app import cli


def test_health_command_runs() -> None:
    runner = CliRunner()

    with patch("integrations.verify.verify_integrations") as mock_verify:
        mock_verify.return_value = [
            {
                "service": "aws",
                "source": "local store",
                "status": "passed",
                "detail": "ok",
            }
        ]

        result = runner.invoke(cli, ["health"])

    assert result.exit_code == 0
    assert "OpenSRE Health" in result.output
    assert "Environment" in result.output
    assert "Integration store" in result.output
    assert "Summary:" in result.output
    assert "1 passed" in result.output
    assert "aws" in result.output


def test_health_command_uses_real_datadog_verification_path(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.verify.resolve_effective_integrations",
        lambda: {
            "datadog": {
                "source": "local store",
                "config": {
                    "api_key": "",
                    "app_key": "",
                    "site": "datadoghq.com",
                    "integration_id": "datadog-local",
                },
            }
        },
    )

    result = runner.invoke(cli, ["health"])

    assert result.exit_code == 0
    assert "Summary:" in result.output
    assert "datadog" in result.output
    assert "MISSING" in result.output
    # The table wraps the detail to its column width, which shifts whenever a
    # longer service name is added; compare with whitespace collapsed.
    assert "Missing API key or application key." in " ".join(result.output.split())


def test_health_command_exits_zero_when_all_integrations_missing() -> None:
    runner = CliRunner()

    with patch("integrations.verify.verify_integrations") as mock_verify:
        mock_verify.return_value = [
            {
                "service": svc,
                "source": "-",
                "status": "missing",
                "detail": "Not configured in local store or env.",
            }
            for svc in ("aws", "datadog", "grafana", "slack")
        ]

        result = runner.invoke(cli, ["health"])

    assert result.exit_code == 0
    assert "4 missing" in result.output


def test_health_command_exits_one_when_any_integration_failed() -> None:
    runner = CliRunner()

    with patch("integrations.verify.verify_integrations") as mock_verify:
        mock_verify.return_value = [
            {
                "service": "aws",
                "source": "local store",
                "status": "passed",
                "detail": "ok",
            },
            {
                "service": "datadog",
                "source": "local store",
                "status": "failed",
                "detail": "401 Unauthorized",
            },
        ]

        result = runner.invoke(cli, ["health"])

    assert result.exit_code == 1
    assert "1 failed" in result.output
