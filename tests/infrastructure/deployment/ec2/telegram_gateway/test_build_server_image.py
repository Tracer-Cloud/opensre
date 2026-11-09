"""Tests for building a server image with the gateway installed."""

from __future__ import annotations

import pytest

import infrastructure.deployment.ec2.telegram_gateway.stack as stack_module
from infrastructure.deployment.ec2.config import GATEWAY_AMI_GIT_REF_ENV
from infrastructure.deployment.ec2.telegram_gateway import build_server_image as image_module

_FAKE_INSTANCE_ID = "i-builder1234567890"
_FAKE_AMI_ID = "ami-0abc1234567890def"


def _stub_image_build_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every external side effect in build_server_image() except AMI id persistence."""
    monkeypatch.setattr(
        image_module,
        "create_instance_profile",
        lambda *_a, **_kw: {
            "ProfileName": "p",
            "ProfileArn": "arn:aws:iam::1:instance-profile/p",
            "RoleName": "r",
        },
    )
    monkeypatch.setattr(image_module, "get_latest_al2023_ami", lambda *_a, **_kw: "ami-base")
    monkeypatch.setattr(
        image_module, "launch_instance", lambda *_a, **_kw: {"InstanceId": _FAKE_INSTANCE_ID}
    )
    monkeypatch.setattr(image_module, "wait_for_running", lambda *_a, **_kw: None)
    monkeypatch.setattr(image_module, "wait_for_ssm_registration", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        image_module,
        "run_ssm_shell_command",
        lambda *_a, **_kw: {"status": "Success", "stderr": ""},
    )
    monkeypatch.setattr(image_module, "create_image_from_instance", lambda *_a, **_kw: _FAKE_AMI_ID)
    monkeypatch.setattr(image_module, "terminate_instance", lambda *_a, **_kw: None)
    monkeypatch.setattr(image_module, "delete_instance_profile", lambda *_a, **_kw: None)


def test_build_server_image_uses_env_var_git_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """build_server_image() uses OPENSRE_GATEWAY_GIT_REF when set."""
    ami_id_file = tmp_path / "gateway-id.txt"
    monkeypatch.setattr(stack_module, "_AMI_ID_FILE", ami_id_file)
    monkeypatch.setenv(GATEWAY_AMI_GIT_REF_ENV, "v0.3.0")
    _stub_image_build_dependencies(monkeypatch)

    captured_commands: list[list[str]] = []

    def capture_ssm(*_a: object, commands: list[str], **_kw: object) -> dict:
        captured_commands.append(commands)
        return {"status": "Success", "stderr": ""}

    monkeypatch.setattr(image_module, "run_ssm_shell_command", capture_ssm)

    result = image_module.build_server_image(ami_id_path=ami_id_file)

    assert result == _FAKE_AMI_ID
    joined = "\n".join(" ".join(c) if isinstance(c, list) else c for c in captured_commands[0])
    assert "v0.3.0" in joined


def test_build_server_image_saves_ami_id_to_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """build_server_image() persists the new AMI id so deploy can reuse it."""
    ami_id_file = tmp_path / "gateway-id.txt"
    monkeypatch.setattr(stack_module, "_AMI_ID_FILE", ami_id_file)
    monkeypatch.delenv(GATEWAY_AMI_GIT_REF_ENV, raising=False)
    _stub_image_build_dependencies(monkeypatch)
    # make _resolve_git_ref return a deterministic value without running git
    monkeypatch.setattr(image_module, "_resolve_git_ref", lambda: "deadbeef1234")

    image_module.build_server_image(ami_id_path=ami_id_file)

    assert ami_id_file.exists()
    assert ami_id_file.read_text().strip() == _FAKE_AMI_ID


def test_build_server_image_terminates_builder_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Builder instance is terminated even when the install script fails."""
    ami_id_file = tmp_path / "gateway-id.txt"
    monkeypatch.setattr(stack_module, "_AMI_ID_FILE", ami_id_file)
    monkeypatch.setenv(GATEWAY_AMI_GIT_REF_ENV, "main")

    terminated: list[str] = []

    monkeypatch.setattr(
        image_module,
        "create_instance_profile",
        lambda *_a, **_kw: {
            "ProfileName": "p",
            "ProfileArn": "arn:p",
            "RoleName": "r",
        },
    )
    monkeypatch.setattr(image_module, "get_latest_al2023_ami", lambda *_a, **_kw: "ami-base")
    monkeypatch.setattr(
        image_module, "launch_instance", lambda *_a, **_kw: {"InstanceId": _FAKE_INSTANCE_ID}
    )
    monkeypatch.setattr(image_module, "wait_for_running", lambda *_a, **_kw: None)
    monkeypatch.setattr(image_module, "wait_for_ssm_registration", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        image_module,
        "run_ssm_shell_command",
        lambda *_a, **_kw: {"status": "Failed", "stderr": "something went wrong"},
    )
    monkeypatch.setattr(
        image_module, "terminate_instance", lambda iid, *_a, **_kw: terminated.append(iid)
    )
    monkeypatch.setattr(image_module, "delete_instance_profile", lambda *_a, **_kw: None)

    with pytest.raises(RuntimeError, match="Install commands failed"):
        image_module.build_server_image(ami_id_path=ami_id_file)

    assert _FAKE_INSTANCE_ID in terminated
