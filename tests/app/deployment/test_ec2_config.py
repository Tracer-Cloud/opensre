from __future__ import annotations

import pytest

from app.deployment.ec2_config import (
    delete_remote_outputs,
    load_remote_outputs,
    save_remote_outputs,
)


def test_remote_outputs_round_trip(tmp_path) -> None:
    path = tmp_path / "tracer-ec2-remote.json"
    outputs = {
        "InstanceId": "i-123",
        "PublicIpAddress": "18.233.154.38",
        "ServerPort": "8080",
    }

    save_remote_outputs(outputs, path=path)

    assert load_remote_outputs(path=path) == outputs


def test_delete_remote_outputs_removes_saved_file(tmp_path) -> None:
    path = tmp_path / "tracer-ec2-remote.json"
    save_remote_outputs({"InstanceId": "i-123"}, path=path)

    delete_remote_outputs(path=path)

    with pytest.raises(FileNotFoundError):
        load_remote_outputs(path=path)
