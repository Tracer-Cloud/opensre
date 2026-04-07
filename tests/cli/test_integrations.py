from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from app.cli.__main__ import cli


def test_integrations_show_redacts_api_token() -> None:
    runner = CliRunner()

    with patch(
        "app.integrations.cli.get_integration",
        return_value={
            "id": "vercel-1234",
            "service": "vercel",
            "status": "active",
            "credentials": {
                "api_token": "vcp_sensitive_token_value",
                "team_id": "team_123",
            },
        },
    ):
        result = runner.invoke(cli, ["integrations", "show", "vercel"])

    assert result.exit_code == 0
    assert "vcp_****" in result.output
    assert "vcp_sensitive_token_value" not in result.output
