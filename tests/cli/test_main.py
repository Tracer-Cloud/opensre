from __future__ import annotations

from unittest.mock import patch

from app.cli.__main__ import main


def test_main_runs_health_command(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda: None)

    with (
        patch("app.integrations.verify.verify_integrations") as mock_verify,
        patch("app.integrations.verify.format_verification_results") as mock_format,
    ):
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

        exit_code = main(["health"])

    assert exit_code == 0
