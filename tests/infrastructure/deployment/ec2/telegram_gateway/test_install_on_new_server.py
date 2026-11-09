"""Tests for installing the gateway onto a newly started server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import infrastructure.deployment.ec2.telegram_gateway.install_on_new_server as install_module
from infrastructure.deployment.ec2.config import EC2_UBUNTU_ROOT_DEVICE_NAME

_INSTANCE_ID = "i-direct123"
_PUBLIC_IP = "1.2.3.4"
_BASE_AMI = "ami-base123"
_FAKE_PROFILE = {
    "ProfileName": "opensre-gateway-direct-profile",
    "ProfileArn": "arn:aws:iam::1:instance-profile/p",
    "RoleName": "opensre-gateway-direct-role",
}


def _stub_deploy(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Stub every external call in install_on_new_server() and return captured SSM calls."""
    calls: dict[str, list[str]] = {"ssm_commands": [], "launch_kwargs": []}

    monkeypatch.setattr(install_module, "create_instance_profile", lambda *_a, **_kw: _FAKE_PROFILE)
    monkeypatch.setattr(install_module, "get_latest_ubuntu2204_ami", lambda _region: _BASE_AMI)
    monkeypatch.setattr(
        install_module, "create_stack_security_group", lambda *_a, **_kw: "sg-direct123"
    )

    def fake_launch_instance(*_args: object, **kwargs: object) -> dict[str, str]:
        calls["launch_kwargs"].append(kwargs)
        return {"InstanceId": _INSTANCE_ID}

    monkeypatch.setattr(install_module, "launch_instance", fake_launch_instance)
    monkeypatch.setattr(
        install_module,
        "wait_for_running",
        lambda *_a, **_kw: {"PublicIpAddress": _PUBLIC_IP},
    )
    monkeypatch.setattr(install_module, "wait_for_ssm_registration", lambda *_a, **_kw: None)

    def fake_run_ssm(instance_id: str, *, commands: list[str], **_kw: object) -> dict[str, str]:
        calls["ssm_commands"].extend(commands)
        return {"status": "Success", "stdout": "", "stderr": ""}

    monkeypatch.setattr(install_module, "run_ssm_shell_command", fake_run_ssm)
    monkeypatch.setattr(install_module, "provision_gateway_via_ssm", lambda *_a, **_kw: None)
    monkeypatch.setattr(install_module, "wait_for_gateway_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(install_module, "_cleanup_existing", lambda **_kw: False)

    return calls


def test_install_on_new_server_returns_all_required_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """install_on_new_server() must return all expected output keys."""
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)
    _stub_deploy(monkeypatch)

    outputs = install_module.install_on_new_server(env_vars={}, region="us-east-1")

    assert outputs["InstanceId"] == _INSTANCE_ID
    assert outputs["PublicIpAddress"] == _PUBLIC_IP
    assert outputs["BaseAmiId"] == _BASE_AMI
    assert outputs["SecurityGroupId"] == "sg-direct123"
    assert "ProfileName" in outputs
    assert "RoleName" in outputs
    # GitRef is no longer in outputs (no git clone with curl installer)
    assert "GitRef" not in outputs


def test_install_on_new_server_persists_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """install_on_new_server() must write outputs to the expected path."""
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)
    _stub_deploy(monkeypatch)

    install_module.install_on_new_server(env_vars={}, region="us-east-1")

    stack_name = install_module._direct_stack_name()
    output_file = tmp_path / f"{stack_name}.json"
    assert output_file.exists()
    data = json.loads(output_file.read_text())
    assert data["InstanceId"] == _INSTANCE_ID


def test_install_on_new_server_uses_ubuntu_root_device_for_volume_resize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ubuntu AMIs use /dev/sda1; direct deploy must pass that root device name."""
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)
    calls = _stub_deploy(monkeypatch)

    install_module.install_on_new_server(env_vars={}, region="us-east-1")

    assert calls["launch_kwargs"]
    assert calls["launch_kwargs"][0]["root_device_name"] == EC2_UBUNTU_ROOT_DEVICE_NAME


def test_install_on_new_server_uses_curl_installer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Install commands must use the curl installer URL, not pip or git."""
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)
    calls = _stub_deploy(monkeypatch)

    install_module.install_on_new_server(env_vars={}, region="us-east-1")

    joined = "\n".join(calls["ssm_commands"])
    assert install_module._INSTALL_URL in joined
    assert "curl" in joined
    assert "pip install" not in joined
    assert "git+" not in joined


def test_install_on_new_server_binary_path_in_service_unit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The embedded systemd unit must use /usr/local/bin/opensre, not the venv path."""
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)
    _stub_deploy(monkeypatch)

    install_module.install_on_new_server(env_vars={}, region="us-east-1")

    assert "/usr/local/bin/opensre" in install_module._SERVICE_UNIT
    assert "/opt/opensre/.venv" not in install_module._SERVICE_UNIT


def test_install_on_new_server_raises_on_install_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """install_on_new_server() must raise RuntimeError when the install SSM command fails."""
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)
    _stub_deploy(monkeypatch)

    monkeypatch.setattr(
        install_module,
        "run_ssm_shell_command",
        lambda *_a, **_kw: {"status": "Failed", "stdout": "", "stderr": "curl error"},
    )

    with pytest.raises(RuntimeError, match="Install commands failed"):
        install_module.install_on_new_server(env_vars={}, region="us-east-1")


def test_destroy_installed_server_terminates_instance_and_cleans_iam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """destroy_installed_server() must terminate the instance and delete the IAM profile."""
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)

    stack_name = install_module._direct_stack_name()
    outputs_file = tmp_path / f"{stack_name}.json"
    outputs_file.write_text(
        json.dumps(
            {
                "InstanceId": _INSTANCE_ID,
                "ProfileName": "opensre-gateway-direct-profile",
                "RoleName": "opensre-gateway-direct-role",
            }
        )
    )

    terminated: list[str] = []
    deleted_profiles: list[str] = []

    monkeypatch.setattr(
        install_module,
        "terminate_instance",
        lambda instance_id, _region: terminated.append(instance_id),
    )
    monkeypatch.setattr(
        install_module,
        "delete_instance_profile",
        lambda profile, _role, _region: deleted_profiles.append(profile),
    )

    results = install_module.destroy_installed_server(region="us-east-1")

    assert _INSTANCE_ID in terminated
    assert deleted_profiles == ["opensre-gateway-direct-profile"]
    assert any("ec2-instance" in r for r in results["deleted"])
    assert not outputs_file.exists()


def test_destroy_installed_server_handles_missing_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """destroy_installed_server() must not crash when no outputs file exists."""
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(install_module, "terminate_instance", lambda *_a, **_kw: None)
    monkeypatch.setattr(install_module, "delete_instance_profile", lambda *_a, **_kw: None)

    results = install_module.destroy_installed_server(region="us-east-1")

    assert isinstance(results["deleted"], list)
    assert isinstance(results["failed"], list)


def test_cleanup_existing_cleans_iam_when_no_outputs_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_cleanup_existing() must delete the IAM profile/role even when no outputs file exists.

    Regression: tagged instances were terminated but the outputs file was absent,
    so destroy_installed_server() was skipped and IAM resources were silently orphaned.
    """
    monkeypatch.setattr(install_module, "_OUTPUTS_DIR", tmp_path)
    # No outputs file — simulates a lost or never-written outputs file.

    terminated: list[str] = []
    deleted_profiles: list[str] = []

    monkeypatch.setattr(
        install_module,
        "find_stack_instance_ids",
        lambda _stack, *, region: ["i-orphan01"],  # noqa: ARG005
    )
    monkeypatch.setattr(
        install_module,
        "terminate_instance",
        lambda instance_id, _region: terminated.append(instance_id),
    )
    monkeypatch.setattr(
        install_module,
        "delete_instance_profile",
        lambda profile, _role, _region: deleted_profiles.append(profile),
    )

    ran = install_module._cleanup_existing(region="us-east-1")

    assert ran is True
    assert "i-orphan01" in terminated
    # IAM must be cleaned up using derived names, not skipped.
    stack_name = install_module._direct_stack_name()
    assert deleted_profiles == [f"{stack_name}-profile"]


def test_stack_name_includes_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_STACK_SUFFIX", "joe")
    assert install_module._direct_stack_name() == "opensre-gateway-direct-joe"


def test_stack_name_default_when_no_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_STACK_SUFFIX", raising=False)
    assert install_module._direct_stack_name() == "opensre-gateway-direct"


def test_curl_install_commands_structure() -> None:
    """_build_curl_install_commands() must contain all required setup steps."""
    commands = install_module._build_curl_install_commands()
    joined = "\n".join(commands)

    assert "set -eu" in joined
    assert "useradd" in joined  # creates opensre user
    assert install_module._INSTALL_URL in joined  # curl installer URL
    assert "/usr/local/bin" in joined  # install dir
    assert "export HOME=/root" in joined  # SSM has no HOME set
    assert "OPENSRE_AUTO_LAUNCH=0" in joined  # no interactive wizard
    assert "systemctl enable opensre-gateway" in joined  # service enabled
    assert "opensre --help" in joined  # smoke-check
