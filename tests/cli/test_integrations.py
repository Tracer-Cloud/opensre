from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from integrations.cli import (
    _HANDLERS,
    _setup_servicenow,
)
from surfaces.cli.app import cli
from surfaces.cli.constants import SETUP_SERVICES, VERIFY_SERVICES


def test_integrations_show_redacts_api_token() -> None:
    runner = CliRunner()

    with patch(
        "integrations.cli.get_integration",
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


def test_integrations_setup_accepts_github() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_setup,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "github"
        result = runner.invoke(cli, ["integrations", "setup", "github"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("github")
    mock_verify.assert_not_called()


def test_integrations_setup_accepts_vercel() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("integrations.cli.cmd_setup", side_effect=SystemExit(1)) as mock_setup,
        patch("integrations.cli.cmd_verify", return_value=1) as mock_verify,
    ):
        mock_setup.return_value = "vercel"
        result = runner.invoke(cli, ["integrations", "setup", "vercel"])

    assert result.exit_code == 1
    mock_setup.assert_called_once_with("vercel")
    mock_verify.assert_not_called()
    mock_capture.assert_not_called()


def test_setup_servicenow_saves_normalized_https_url(monkeypatch) -> None:
    answers = iter(["https://dev12345.service-now.com/", "admin", "s3cret"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    class _Resp:
        status_code = 200

    saved: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr("integrations.cli._p", fake_p)
    monkeypatch.setattr(
        "integrations.setup_flow.upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )
    monkeypatch.setattr(
        "integrations.setup_flow.sync_env_secret",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "integrations.setup_flow.sync_env_values",
        lambda *_args, **_kwargs: Path("/tmp/.env"),
    )
    monkeypatch.setattr(
        "integrations.servicenow.verifier.httpx.get",
        lambda *_args, **_kwargs: _Resp(),
    )

    _setup_servicenow()

    assert _HANDLERS["servicenow"] is _setup_servicenow
    assert saved == [
        (
            "servicenow",
            {
                "credentials": {
                    "instance_url": "https://dev12345.service-now.com",
                    "username": "admin",
                    "password": "s3cret",
                }
            },
        )
    ]


def test_setup_servicenow_rejects_plain_http_remote_url(monkeypatch) -> None:
    # Regression for the silent-save gap: a plain-http remote URL must fail at
    # setup with an actionable error, not be stored and dropped at classify.
    monkeypatch.setattr(
        "integrations.cli._p",
        lambda *_args, **_kwargs: "http://dev12345.service-now.com",
    )
    saved: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "integrations.setup_flow.upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )
    monkeypatch.setattr(
        "integrations.setup_flow.sync_env_secret",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "integrations.setup_flow.sync_env_values",
        lambda *_args, **_kwargs: Path("/tmp/.env"),
    )

    with pytest.raises(SystemExit):
        _setup_servicenow()

    assert saved == []


def test_integrations_setup_accepts_telegram() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_setup,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "telegram"
        result = runner.invoke(cli, ["integrations", "setup", "telegram"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("telegram")
    mock_verify.assert_not_called()


def test_integrations_setup_accepts_whatsapp() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_setup,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "whatsapp"
        result = runner.invoke(cli, ["integrations", "setup", "whatsapp"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("whatsapp")
    mock_verify.assert_not_called()


def test_integrations_setup_accepts_twilio() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_setup,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "twilio"
        result = runner.invoke(cli, ["integrations", "setup", "twilio"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("twilio")
    mock_verify.assert_not_called()


def test_integrations_setup_accepts_smtp() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_setup,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "smtp"
        result = runner.invoke(cli, ["integrations", "setup", "smtp"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("smtp")
    mock_verify.assert_not_called()


def test_integrations_setup_skips_auto_verify_for_unverifiable_service() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_setup,
        patch("integrations.cli.cmd_verify") as mock_verify,
    ):
        # rds is registered in SETUP_SERVICES but intentionally absent from
        # VERIFY_SERVICES, so it exercises the auto-verify-skip path.
        # (opensearch was used here previously but moved into VERIFY_SERVICES
        # by PR #1143, which is why this assertion was updated.)
        mock_setup.return_value = "rds"
        result = runner.invoke(cli, ["integrations", "setup", "rds"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("rds")
    mock_verify.assert_not_called()


def test_integrations_verify_accepts_github() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "github"])

    assert result.exit_code == 0
    mock_verify.assert_called_once_with(
        "github",
        send_slack_test=False,
    )
    mock_capture.assert_called_once_with("github")


def test_integrations_verify_accepts_argocd() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "argocd"])

    assert result.exit_code == 0
    mock_verify.assert_called_once_with(
        "argocd",
        send_slack_test=False,
    )
    mock_capture.assert_called_once_with("argocd")


def test_integrations_setup_accepts_helm() -> None:
    # Regression test for #1973: helm had verify wired in the registry but no
    # setup_order / _setup_helm handler, so Click rejected the positional arg.
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("integrations.cli.cmd_setup") as mock_setup,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "helm"
        result = runner.invoke(cli, ["integrations", "setup", "helm"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("helm")
    mock_verify.assert_not_called()
    mock_capture.assert_called_once_with("helm")


def test_integrations_verify_accepts_helm() -> None:
    # Regression test for #1973: helm was registered in the runtime registry
    # but rejected by Click because the CLI's hardcoded VERIFY_SERVICES tuple
    # had drifted out of sync.
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "helm"])

    assert result.exit_code == 0
    mock_verify.assert_called_once_with(
        "helm",
        send_slack_test=False,
    )
    mock_capture.assert_called_once_with("helm")


def test_integrations_verify_accepts_servicenow() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "servicenow"])

    assert result.exit_code == 0
    mock_verify.assert_called_once_with(
        "servicenow",
        send_slack_test=False,
    )
    mock_capture.assert_called_once_with("servicenow")


def test_integrations_setup_accepts_servicenow() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_setup,
        patch("integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "servicenow"
        result = runner.invoke(cli, ["integrations", "setup", "servicenow"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("servicenow")
    mock_verify.assert_not_called()


def test_verify_services_includes_previously_missing_integrations() -> None:
    # #1973 surfaced these names as registered in the runtime registry but
    # rejected by Click's positional-arg validator (the CLI's hardcoded
    # VERIFY_SERVICES tuple had drifted). Anchor them here so a revert to a
    # hardcoded tuple — or accidental removal from the registry — fails this
    # test loudly.
    previously_missing = {
        "azure",
        "azure_sql",
        "helm",
        "openobserve",
        "snowflake",
        "splunk",
        "supabase",
    }
    assert previously_missing <= set(VERIFY_SERVICES)


def test_setup_services_includes_previously_missing_integrations() -> None:
    # #2537: telegram, whatsapp and twilio had handlers and registry entries
    # but were rejected by Click because the CLI's hardcoded SETUP_SERVICES
    # tuple had drifted. Anchor them here so a revert to a hardcoded tuple —
    # or accidental removal from the registry — fails this test loudly.
    previously_missing = {"telegram", "twilio", "whatsapp"}
    assert previously_missing <= set(SETUP_SERVICES)
