from __future__ import annotations

import pytest

from tests.deployment.ec2.infrastructure_sdk import deploy as deploy_module


def test_deploy_returns_all_required_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deploy() must return InstanceId, PublicIpAddress, and infrastructure keys."""

    def fake_get_default_vpc(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"vpc_id": "vpc-123"}

    def fake_get_public_subnets(*_args: object, **_kwargs: object) -> list[str]:
        return ["subnet-123"]

    def fake_create_security_group(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"group_id": "sg-123"}

    def fake_create_instance_profile(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {
            "ProfileName": "profile-123",
            "ProfileArn": "arn:aws:iam::123:instance-profile/profile-123",
            "RoleName": "role-123",
        }

    def fake_get_latest_ami(*_args: object, **_kwargs: object) -> str:
        return "ami-123"

    def fake_generate_user_data(*_args: object, **_kwargs: object) -> str:
        return "#!/bin/bash"

    def fake_launch_instance(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"InstanceId": "i-123"}

    def fake_wait_for_running(
        instance_id: str, *_args: object, **_kwargs: object
    ) -> dict[str, str]:
        return {"InstanceId": instance_id, "PublicIpAddress": "54.1.2.3"}

    def fake_wait_for_health(*_args: object, **_kwargs: object) -> bool:
        return True

    def fake_save_outputs(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(deploy_module, "get_default_vpc", fake_get_default_vpc)
    monkeypatch.setattr(deploy_module, "get_public_subnets", fake_get_public_subnets)
    monkeypatch.setattr(deploy_module, "create_security_group", fake_create_security_group)
    monkeypatch.setattr(deploy_module, "create_instance_profile", fake_create_instance_profile)
    monkeypatch.setattr(deploy_module, "get_latest_al2023_ami", fake_get_latest_ami)
    monkeypatch.setattr(deploy_module, "generate_user_data", fake_generate_user_data)
    monkeypatch.setattr(deploy_module, "launch_instance", fake_launch_instance)
    monkeypatch.setattr(deploy_module, "wait_for_running", fake_wait_for_running)
    monkeypatch.setattr(deploy_module, "wait_for_health", fake_wait_for_health)
    monkeypatch.setattr(deploy_module, "save_outputs", fake_save_outputs)

    outputs = deploy_module.deploy()

    assert outputs["PublicIpAddress"] == "54.1.2.3"
    assert outputs["InstanceId"] == "i-123"
    assert outputs["SecurityGroupId"] == "sg-123"
