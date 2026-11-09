"""``opensre integrations setup aws`` — the surface behind the shell's /integrations setup.

The wizard already gated the role mode; the CLI did not, so a shell user with
no ambient credentials still typed an ARN and hit "Unable to locate
credentials". Both surfaces now share :mod:`integrations.aws.role_mode_gate`.
"""

from __future__ import annotations

from typing import Any

import pytest

import integrations.cli as cli
from integrations.aws import role_mode_gate as gate


@pytest.fixture
def no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: False)


def _script_cli(
    monkeypatch: pytest.MonkeyPatch, *, mode_pick: str, gate_pick: str | None, answers: list[str]
) -> dict[str, Any]:
    """Drive the CLI prompts: mode picker, then the gate question, then fields."""
    seen: dict[str, Any] = {"selects": [], "prompts": [], "printed": []}
    picks = [mode_pick, gate_pick]

    def _fake_select(message: str, choices: list[Any], **_kw: Any) -> Any:
        seen["selects"].append(message)
        return picks.pop(0)

    def _fake_p(label: str, default: str = "", secret: bool = False) -> str:
        seen["prompts"].append(label)
        return answers.pop(0)

    monkeypatch.setattr(cli, "_select", _fake_select)
    monkeypatch.setattr(cli, "_p", _fake_p)
    monkeypatch.setattr(
        "builtins.print", lambda *a, **_k: seen["printed"].append(" ".join(map(str, a)))
    )
    return seen


@pytest.mark.usefixtures("no_ambient_credentials")
def test_role_pick_without_credentials_asks_the_gate_and_can_switch_to_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange — user picks the role mode, then "use keys instead"; keys prompts follow
    import dataclasses

    import integrations.aws.setup as aws_setup

    verified: list[dict[str, Any]] = []

    def _fake_verify(_source: str, config: dict[str, Any]) -> dict[str, str]:
        verified.append(dict(config))
        return {"status": "passed", "detail": "ok"}

    monkeypatch.setattr(
        aws_setup, "AWS_SETUP", dataclasses.replace(aws_setup.AWS_SETUP, verify=_fake_verify)
    )
    monkeypatch.setattr("integrations.setup_flow.upsert_integration", lambda *_a: None)
    monkeypatch.setattr("integrations.setup_flow.sync_env_secret", lambda *_a: None)
    monkeypatch.setattr("integrations.setup_flow.sync_env_values", lambda *_a, **_k: "/tmp/.env")
    monkeypatch.setattr(
        "integrations.setup_flow.push_webapp_org_integration", lambda *_a, **_k: None
    )
    seen = _script_cli(
        monkeypatch,
        mode_pick=gate.ROLE_MODE,
        gate_pick=str(gate.RoleGateChoice.USE_KEYS),
        answers=["us-east-1", "AKIAEXAMPLE", "secret", ""],  # region, key id, secret, token
    )

    # Act
    cli._setup_aws()

    # Assert — the gate question was asked, and only keys-mode fields were prompted
    assert seen["selects"] == ["AWS authentication method:", gate.GATE_QUESTION]
    assert "IAM Role ARN" not in seen["prompts"]
    assert "AWS_ACCESS_KEY_ID" in seen["prompts"]
    assert verified and verified[0].get("access_key_id") == "AKIAEXAMPLE"


@pytest.mark.usefixtures("no_ambient_credentials")
def test_configure_first_prints_the_instruction_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    seen = _script_cli(
        monkeypatch,
        mode_pick=gate.ROLE_MODE,
        gate_pick=str(gate.RoleGateChoice.CONFIGURE_FIRST),
        answers=[],
    )

    # Act
    with pytest.raises(SystemExit) as exit_info:
        cli._setup_aws()

    # Assert — a clean, deliberate exit with the way back, not an error
    assert exit_info.value.code == 0
    assert any(gate.CONFIGURE_FIRST_INSTRUCTION in line for line in seen["printed"])
    assert seen["prompts"] == []


def test_an_invalid_role_arn_is_asked_again_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role ID pasted as the ARN is refused at the prompt, not by STS later."""
    import dataclasses

    import integrations.aws.setup as aws_setup

    # Arrange — credentials present so the gate is silent; first ARN answer is the
    # role's unique ID, second is a real ARN
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: True)
    verified: list[dict[str, Any]] = []

    def _fake_verify(_source: str, config: dict[str, Any]) -> dict[str, str]:
        verified.append(dict(config))
        return {"status": "passed", "detail": "ok"}

    monkeypatch.setattr(
        aws_setup, "AWS_SETUP", dataclasses.replace(aws_setup.AWS_SETUP, verify=_fake_verify)
    )
    monkeypatch.setattr("integrations.setup_flow.upsert_integration", lambda *_a: None)
    monkeypatch.setattr("integrations.setup_flow.sync_env_secret", lambda *_a: None)
    monkeypatch.setattr("integrations.setup_flow.sync_env_values", lambda *_a, **_k: "/tmp/.env")
    monkeypatch.setattr(
        "integrations.setup_flow.push_webapp_org_integration", lambda *_a, **_k: None
    )
    seen = _script_cli(
        monkeypatch,
        mode_pick=gate.ROLE_MODE,
        gate_pick=None,
        answers=[
            "us-east-1",
            "AROAVYB3PA5RPMNFXNNJM",  # rejected
            "arn:aws:iam::123456789012:role/OpenSREReadOnly",  # accepted
            "",  # external id
        ],
    )

    # Act
    cli._setup_aws()

    # Assert — the ARN prompt ran twice, the reason was printed, and setup went on
    assert seen["prompts"].count("IAM Role ARN") == 2
    assert any("unique ID" in line for line in seen["printed"])
    assert verified and verified[0]["role_arn"] == "arn:aws:iam::123456789012:role/OpenSREReadOnly"
