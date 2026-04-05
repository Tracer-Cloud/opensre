from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.remote.client import RemoteRunResult
from tests.deployment.ec2.infrastructure_sdk import deploy as deploy_module


def test_validate_remote_agent_requires_completed_stream() -> None:
    client = MagicMock()
    client.health.return_value = {"ok": True}
    client.run_streamed_investigation.return_value = RemoteRunResult(
        thread_id="thread-123",
        events_received=2,
        node_names_seen=["extract_alert"],
        saw_end=False,
        final_state={"root_cause": "broken"},
    )

    with (
        patch("app.remote.client.RemoteAgentClient", return_value=client),
        pytest.raises(RuntimeError, match="did not reach the end event"),
    ):
        deploy_module._validate_remote_agent("10.0.0.1")


def test_deploy_saves_remote_url_only_after_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    call_order: list[str] = []

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
            "RoleArn": "arn:aws:iam::123:role/role-123",
        }

    def fake_ensure_repository(*_args: object, **_kwargs: object) -> str:
        return "123456789012.dkr.ecr.us-east-1.amazonaws.com/tracer-ec2/opensre"

    def fake_build_and_push(*_args: object, **_kwargs: object) -> str:
        return "123456789012.dkr.ecr.us-east-1.amazonaws.com/tracer-ec2/opensre:latest"

    def fake_grant_ec2_pull(*_args: object, **_kwargs: object) -> None:
        pass

    def fake_get_latest_ami(*_args: object, **_kwargs: object) -> str:
        return "ami-123"

    def fake_generate_user_data(*_args: object, **_kwargs: object) -> str:
        return "#!/bin/bash"

    def fake_launch_instance(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"InstanceId": "i-123"}

    def fake_wait_for_running(instance_id: str, *_args: object, **_kwargs: object) -> dict[str, str]:
        return {"InstanceId": instance_id, "PublicIpAddress": "54.1.2.3"}

    def fake_wait_for_health(*_args: object, **_kwargs: object) -> None:
        call_order.append("wait_for_health")

    def fake_validate_remote_agent(*_args: object, **_kwargs: object) -> tuple[int, list[str]]:
        call_order.append("validate_remote_agent")
        return 4, ["extract_alert"]

    def fake_save_outputs(*_args: object, **_kwargs: object) -> None:
        call_order.append("save_outputs")

    def fake_normalize_url(*_args: object, **_kwargs: object) -> str:
        return "http://54.1.2.3:2024"

    def fake_save_remote_url(*_args: object, **_kwargs: object) -> Path:
        call_order.append("save_remote_url")
        return tmp_path / "opensre.json"

    monkeypatch.setattr(deploy_module, "get_default_vpc", fake_get_default_vpc)
    monkeypatch.setattr(deploy_module, "get_public_subnets", fake_get_public_subnets)
    monkeypatch.setattr(deploy_module, "create_security_group", fake_create_security_group)
    monkeypatch.setattr(deploy_module, "create_instance_profile", fake_create_instance_profile)
    monkeypatch.setattr(deploy_module, "ensure_repository", fake_ensure_repository)
    monkeypatch.setattr(deploy_module, "build_and_push", fake_build_and_push)
    monkeypatch.setattr(deploy_module, "grant_ec2_pull", fake_grant_ec2_pull)
    monkeypatch.setattr(deploy_module, "get_latest_al2023_ami", fake_get_latest_ami)
    monkeypatch.setattr(deploy_module, "generate_user_data", fake_generate_user_data)
    monkeypatch.setattr(deploy_module, "launch_instance", fake_launch_instance)
    monkeypatch.setattr(deploy_module, "wait_for_running", fake_wait_for_running)
    monkeypatch.setattr(deploy_module, "wait_for_health", fake_wait_for_health)
    monkeypatch.setattr(deploy_module, "_validate_remote_agent", fake_validate_remote_agent)
    monkeypatch.setattr(deploy_module, "save_outputs", fake_save_outputs)
    monkeypatch.setattr("app.remote.client.normalize_url", fake_normalize_url)
    monkeypatch.setattr("app.cli.wizard.store.save_remote_url", fake_save_remote_url)

    outputs = deploy_module.deploy()

    assert outputs["PublicIpAddress"] == "54.1.2.3"
    assert outputs["ImageUri"].endswith(":latest")
    assert call_order.index("validate_remote_agent") < call_order.index("save_remote_url")
