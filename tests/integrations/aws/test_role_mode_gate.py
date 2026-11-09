"""The AWS role-mode gate: shared decision for the CLI and the wizard."""

from __future__ import annotations

import pytest

from integrations.aws import role_mode_gate as gate


def test_role_mode_is_untouched_when_the_chain_has_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: True)
    asked: list[bool] = []

    # Act
    mode = gate.gate_role_mode(gate.ROLE_MODE, ask=lambda: asked.append(True) or None)

    # Assert — no question asked
    assert mode == gate.ROLE_MODE
    assert asked == []


def test_keys_mode_never_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: False)
    asked: list[bool] = []

    mode = gate.gate_role_mode(gate.KEYS_MODE, ask=lambda: asked.append(True) or None)

    assert mode == gate.KEYS_MODE
    assert asked == []


@pytest.mark.parametrize(
    ("choice", "expected_mode"),
    [
        (gate.RoleGateChoice.USE_KEYS, gate.KEYS_MODE),
        (gate.RoleGateChoice.CONTINUE_ROLE, gate.ROLE_MODE),
    ],
)
def test_empty_chain_steers_by_the_users_choice(
    monkeypatch: pytest.MonkeyPatch, choice: gate.RoleGateChoice, expected_mode: str
) -> None:
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: False)

    mode = gate.gate_role_mode(gate.ROLE_MODE, ask=lambda: choice)

    assert mode == expected_mode


@pytest.mark.parametrize("choice", [gate.RoleGateChoice.CONFIGURE_FIRST, None])
def test_configure_first_or_cancel_leaves_setup(
    monkeypatch: pytest.MonkeyPatch, choice: gate.RoleGateChoice | None
) -> None:
    """Both mean "not now": the surface prints the instruction and exits cleanly."""
    monkeypatch.setattr(gate, "has_ambient_credentials", lambda: False)

    with pytest.raises(gate.ConfigureCredentialsFirst):
        gate.gate_role_mode(gate.ROLE_MODE, ask=lambda: choice)
