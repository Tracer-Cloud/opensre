"""AWS wizard: the role mode is gated on the ambient credential chain.

The decision lives in :mod:`integrations.aws.role_mode_gate` (shared with the
CLI); these tests pin the wizard's adapter — how it asks, and how it leaves.
"""

from __future__ import annotations

from typing import Any

import pytest

import surfaces.cli.wizard.configurators.aws as aws_configurator
from integrations.aws import role_mode_gate as gate
from surfaces.cli.wizard.components import WizardBack


@pytest.fixture
def quiet_console(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    printed: list[str] = []
    monkeypatch.setattr(
        aws_configurator.console, "print", lambda *args, **_kw: printed.append(str(args[0]))
    )
    return printed


def _pick(monkeypatch: pytest.MonkeyPatch, value: str) -> list[dict[str, Any]]:
    """Stub the wizard picker to answer *value* and record what was offered."""
    offered: list[dict[str, Any]] = []

    def _fake_choose(prompt: str, choices: list[Any], **kwargs: Any) -> str:
        offered.append({"prompt": prompt, "values": [c.value for c in choices], **kwargs})
        return value

    monkeypatch.setattr(aws_configurator, "choose", _fake_choose)
    return offered


def test_role_mode_passes_through_when_ambient_credentials_exist(
    monkeypatch: pytest.MonkeyPatch, quiet_console: list[str]
) -> None:
    # Arrange — a profile / env keys / instance role is present
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: True)
    offered = _pick(monkeypatch, "unused")

    # Act
    mode = aws_configurator._gate_aws_mode(gate.ROLE_MODE)

    # Assert — no notice, no extra question
    assert mode == gate.ROLE_MODE
    assert offered == []
    assert quiet_console == []


def test_keys_mode_is_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange — even with no ambient chain, static keys need no prerequisite
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: False)
    offered = _pick(monkeypatch, "unused")

    # Act
    mode = aws_configurator._gate_aws_mode(gate.KEYS_MODE)

    # Assert
    assert mode == gate.KEYS_MODE
    assert offered == []


def test_missing_chain_explains_and_steers_to_keys_by_default(
    monkeypatch: pytest.MonkeyPatch, quiet_console: list[str]
) -> None:
    # Arrange — bare laptop; user accepts the recommended way forward
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: False)
    offered = _pick(monkeypatch, str(gate.RoleGateChoice.USE_KEYS))

    # Act
    mode = aws_configurator._gate_aws_mode(gate.ROLE_MODE)

    # Assert — the user was told why, offered all three paths, and lands on keys
    assert mode == gate.KEYS_MODE
    assert any(gate.NO_AMBIENT_CREDENTIALS_NOTICE in line for line in quiet_console)
    assert offered[0]["default"] == str(gate.RoleGateChoice.USE_KEYS)
    assert set(offered[0]["values"]) == {str(c) for c in gate.RoleGateChoice}


def test_choosing_to_configure_credentials_first_leaves_setup_cleanly(
    monkeypatch: pytest.MonkeyPatch, quiet_console: list[str]
) -> None:
    """The wizard treats WizardBack as 'skipped' — no dead-end retry loop."""
    # Arrange
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: False)
    _pick(monkeypatch, str(gate.RoleGateChoice.CONFIGURE_FIRST))

    # Act / Assert
    with pytest.raises(WizardBack):
        aws_configurator._gate_aws_mode(gate.ROLE_MODE)
    assert any(gate.CONFIGURE_FIRST_INSTRUCTION in line for line in quiet_console)


def test_user_may_insist_on_the_role_for_an_instance_role_deployment(
    monkeypatch: pytest.MonkeyPatch, quiet_console: list[str]
) -> None:
    # Arrange — user knows OpenSRE will run on EC2/ECS with an attached role
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: False)
    _pick(monkeypatch, str(gate.RoleGateChoice.CONTINUE_ROLE))

    # Act
    mode = aws_configurator._gate_aws_mode(gate.ROLE_MODE)

    # Assert
    assert mode == gate.ROLE_MODE


def test_role_mode_requires_the_arn_and_keys_mode_requires_id_and_secret() -> None:
    """The prompt refuses a blank ARN in role mode — no pydantic dead end."""
    from integrations.aws.setup import (
        ACCESS_KEY_ID_FIELD,
        AWS_SETUP,
        EXTERNAL_ID_FIELD,
        ROLE_ARN_FIELD,
        SECRET_ACCESS_KEY_FIELD,
        SESSION_TOKEN_FIELD,
    )

    by_name = {field.name: field for field in AWS_SETUP.fields}

    assert AWS_SETUP.is_required(by_name[ROLE_ARN_FIELD], gate.ROLE_MODE) is True
    assert AWS_SETUP.is_required(by_name[EXTERNAL_ID_FIELD], gate.ROLE_MODE) is False
    assert AWS_SETUP.is_required(by_name[ACCESS_KEY_ID_FIELD], gate.KEYS_MODE) is True
    assert AWS_SETUP.is_required(by_name[SECRET_ACCESS_KEY_FIELD], gate.KEYS_MODE) is True
    assert AWS_SETUP.is_required(by_name[SESSION_TOKEN_FIELD], gate.KEYS_MODE) is False
    # And each mode's fields stay optional under the other mode (they are cleared there).
    assert AWS_SETUP.is_required(by_name[ROLE_ARN_FIELD], gate.KEYS_MODE) is False
