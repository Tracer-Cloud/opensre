from __future__ import annotations

from unittest.mock import patch

from app.cli.commands.deploy import _get_deployment_status


def test_get_deployment_status_reads_remote_outputs() -> None:
    with patch(
        "app.cli.commands.deploy.load_remote_outputs",
        return_value={
            "InstanceId": "i-123",
            "PublicIpAddress": "18.233.154.38",
            "ServerPort": "8080",
        },
    ):
        result = _get_deployment_status()

    assert result == {
        "ip": "18.233.154.38",
        "instance_id": "i-123",
        "port": "8080",
    }


def test_get_deployment_status_returns_empty_when_outputs_missing() -> None:
    with patch(
        "app.cli.commands.deploy.load_remote_outputs",
        side_effect=FileNotFoundError,
    ):
        result = _get_deployment_status()

    assert result == {}
