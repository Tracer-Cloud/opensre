from unittest.mock import patch

from click.testing import CliRunner

from app.cli.__main__ import cli


def test_health_command_runs() -> None:
    runner = CliRunner()

    with patch("app.integrations.verify.verify_integrations") as mock_verify, patch(
        "app.integrations.verify.format_verification_results"
    ) as mock_format:
        mock_verify.return_value = [
            {
                "service": "aws",
                "source": "local store",
                "status": "passed",
                "detail": "ok",
            }
        ]
        mock_format.return_value = (
            "\n"
            "  SERVICE    SOURCE       STATUS      DETAIL\n"
            "  aws        local store  passed      ok\n"
        )

        result = runner.invoke(cli, ["health"])

    assert result.exit_code == 0
    assert "OpenSRE Health" in result.output
    assert "environment:" in result.output
    assert "integration store:" in result.output
    assert "aws" in result.output
