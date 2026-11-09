"""Tests for gateway AMI SSM provisioning."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from infrastructure.deployment.ec2.telegram_gateway import provision as provision_module
from infrastructure.deployment.ec2.telegram_gateway.provision import provision_gateway_via_ssm

_INSTANCE_ID = "i-gateway1234567890"


@patch("infrastructure.deployment.ec2.telegram_gateway.provision.run_ssm_shell_command")
def test_provision_writes_env_file_and_restarts_service(mock_ssm):
    """provision_gateway_via_ssm() writes the env file and restarts opensre-gateway."""
    mock_ssm.return_value = {"status": "Success", "stderr": ""}

    provision_gateway_via_ssm(
        _INSTANCE_ID,
        env_vars={
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "OPENAI_API_KEY": "sk-openai",
        },
    )

    assert mock_ssm.call_count == 1
    commands = mock_ssm.call_args.kwargs["commands"]
    joined = "\n".join(commands)

    assert "set -eu" in joined
    assert "pipefail" not in joined
    assert "base64 -d" in joined
    assert "gateway.env" in joined
    assert "systemctl restart" in joined
    assert "opensre-gateway" in joined


@patch("infrastructure.deployment.ec2.telegram_gateway.provision.run_ssm_shell_command")
def test_provision_secrets_not_in_plain_commands(mock_ssm):
    """Sensitive values must not appear as plain text in the SSM command list."""
    mock_ssm.return_value = {"status": "Success", "stderr": ""}
    secret_token = "tg-secret-token-abc'quote"

    provision_gateway_via_ssm(
        _INSTANCE_ID,
        env_vars={"TELEGRAM_BOT_TOKEN": secret_token},
    )

    commands = mock_ssm.call_args.kwargs["commands"]
    joined = "\n".join(commands)
    assert secret_token not in joined


@patch("infrastructure.deployment.ec2.telegram_gateway.provision.run_ssm_shell_command")
def test_provision_adds_mode_gateway(mock_ssm):
    """provision_gateway_via_ssm() always injects MODE=gateway into the env file."""
    mock_ssm.return_value = {"status": "Success", "stderr": ""}

    provision_gateway_via_ssm(_INSTANCE_ID, env_vars={})

    commands = mock_ssm.call_args.kwargs["commands"]
    # The env file is base64-encoded; extract and decode to verify MODE=gateway
    import base64
    import re

    joined = "\n".join(commands)
    # shlex.quote may or may not add quotes depending on whether the string needs them
    b64_matches = re.findall(r"echo\s+([A-Za-z0-9+/=]+)\s*\|", joined)
    if not b64_matches:
        # try single-quoted form
        b64_matches = re.findall(r"echo\s+'([A-Za-z0-9+/=]+)'\s*\|", joined)
    decoded = "".join(base64.b64decode(m).decode() for m in b64_matches)
    assert "MODE=gateway" in decoded


@patch("infrastructure.deployment.ec2.telegram_gateway.provision.run_ssm_shell_command")
def test_provision_raises_on_ssm_failure(mock_ssm):
    """provision_gateway_via_ssm() raises RuntimeError on SSM failure."""
    mock_ssm.return_value = {"status": "Failed", "stderr": "something broke"}

    with pytest.raises(RuntimeError, match="Failed to provision gateway"):
        provision_gateway_via_ssm(_INSTANCE_ID, env_vars={})


@patch("infrastructure.deployment.ec2.telegram_gateway.provision.run_ssm_shell_command")
@patch("infrastructure.deployment.ec2.telegram_gateway.provision.time.sleep", return_value=None)
def test_wait_for_gateway_ready_returns_when_active_and_sentinel(mock_sleep, mock_ssm):
    """wait_for_gateway_ready() returns once the service is active and logs the sentinel."""
    mock_ssm.return_value = {
        "status": "Success",
        "stdout": "active\nsome log line\n[gateway] ready\n",
        "stderr": "",
    }

    provision_module.wait_for_gateway_ready(_INSTANCE_ID)

    assert mock_ssm.call_count >= 1


@patch("infrastructure.deployment.ec2.telegram_gateway.provision.run_ssm_shell_command")
@patch("infrastructure.deployment.ec2.telegram_gateway.provision.time.sleep", return_value=None)
def test_wait_for_gateway_ready_accepts_slack_sentinel(mock_sleep, mock_ssm):
    """Legacy Slack Socket Mode log line still satisfies the ready wait."""
    mock_ssm.return_value = {
        "status": "Success",
        "stdout": "active\n[slack-gateway] socket mode connected\n",
        "stderr": "",
    }

    provision_module.wait_for_gateway_ready(_INSTANCE_ID)

    assert mock_ssm.call_count >= 1


@patch("infrastructure.deployment.ec2.telegram_gateway.provision.run_ssm_shell_command")
@patch("infrastructure.deployment.ec2.telegram_gateway.provision.time.sleep", return_value=None)
def test_wait_for_gateway_ready_accepts_telegram_sentinel(mock_sleep, mock_ssm):
    """Legacy Telegram polling log line still satisfies the ready wait."""
    mock_ssm.return_value = {
        "status": "Success",
        "stdout": "active\n[telegram-gateway] polling started\n",
        "stderr": "",
    }

    provision_module.wait_for_gateway_ready(_INSTANCE_ID)

    assert mock_ssm.call_count >= 1


@patch("infrastructure.deployment.ec2.telegram_gateway.provision.run_ssm_shell_command")
@patch("infrastructure.deployment.ec2.telegram_gateway.provision.time.sleep", return_value=None)
def test_wait_for_gateway_ready_raises_on_timeout(mock_sleep, mock_ssm):
    """wait_for_gateway_ready() raises TimeoutError when sentinel never appears."""
    mock_ssm.return_value = {
        "status": "Success",
        "stdout": "inactive\n",
        "stderr": "",
    }

    with pytest.raises(TimeoutError, match="did not become ready"):
        provision_module.wait_for_gateway_ready(_INSTANCE_ID, max_attempts=2)
