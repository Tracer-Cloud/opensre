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
    assert "Missing API key or application key." in result.output


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


def test_health_command_rejects_non_positive_rate() -> None:
    runner = CliRunner()

    for invalid_rate in ["0", "-1", "-10"]:
        result = runner.invoke(cli, ["health", "--rate", invalid_rate])
        assert result.exit_code == 2
        assert "Invalid value for '--rate'" in result.output
        assert "is not in the range x>=1" in result.output

        result_watch = runner.invoke(cli, ["health", "--watch", "--rate", invalid_rate])
        assert result_watch.exit_code == 2
        assert "Invalid value for '--rate'" in result_watch.output
        assert "is not in the range x>=1" in result_watch.output


def test_health_command_watch_accepts_valid_rate() -> None:
    runner = CliRunner()
    with (
        patch("integrations.verify.verify_integrations", return_value=[]),
        patch("time.sleep", side_effect=KeyboardInterrupt) as mock_sleep,
    ):
        result = runner.invoke(cli, ["health", "--watch", "--rate", "2"])

    assert result.exit_code == 0
    mock_sleep.assert_called_once_with(2)
