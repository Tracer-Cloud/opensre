"""Behavior of the wizard's shared spec-driven collection loop.

``configure_from_spec`` is what every spec-backed configurator delegates to, so
the prompt-level rules live here rather than being re-asserted per vendor: what
each field is prefilled with, that a blank answer to a defaulted field is
accepted rather than re-prompted, and that a failed verification re-asks instead
of dropping the user out of onboarding.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
import surfaces.cli.wizard.configurators.spec_configurator as spec_configurator

_ENV_PATH = Path("/tmp/opensre-test/.env")

_SPEC = setup_flow.IntegrationSetupSpec(
    service="demo",
    fields=(
        setup_flow.SetupField(
            name="api_key", label="Demo API key", env_var="DEMO_API_KEY", secret=True
        ),
        setup_flow.SetupField(
            name="site", label="Demo site", env_var="DEMO_SITE", default="demo.example.com"
        ),
        setup_flow.SetupField(name="note", label="Demo note", env_var="DEMO_NOTE", required=False),
    ),
    verify=lambda _source, _config: {"status": "passed", "detail": "Demo connected."},
)


@dataclasses.dataclass
class _Run:
    """What the loop asked for, and what it answered with."""

    answers: dict[str, str] = dataclasses.field(default_factory=dict)
    stored: dict[str, Any] = dataclasses.field(default_factory=dict)
    asked: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> _Run:
    state = _Run()

    def _fake_prompt_value(
        label: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = False,
        **_kw: Any,
    ) -> str:
        state.asked.append(
            {"label": label, "default": default, "secret": secret, "allow_empty": allow_empty}
        )
        # Mirror the real prompt_value: a blank answer falls back to the default.
        return state.answers.get(label, "") or default

    monkeypatch.setattr(spec_configurator, "prompt_value", _fake_prompt_value)
    monkeypatch.setattr(spec_configurator, "integration_defaults", lambda _s: ({}, state.stored))
    monkeypatch.setattr(spec_configurator, "render_integration_result", lambda *_a: None)
    monkeypatch.setattr(setup_flow, "upsert_integration", lambda *_a: None)
    monkeypatch.setattr(setup_flow, "sync_env_secret", lambda *_a: None)
    monkeypatch.setattr(setup_flow, "sync_env_values", lambda *_a, **_kw: _ENV_PATH)
    return state


def test_blank_answer_to_a_defaulted_field_is_accepted(run: _Run) -> None:
    """The rule the ``allow_empty`` argument depends on.

    ``allow_empty=False`` is passed for required fields, defaulted or not, and is
    only consulted when there is no default to fall back on. Pressing enter on a
    defaulted field must therefore succeed rather than loop on "Required.".
    """
    run.answers = {"Demo API key": "key-1"}

    title, env_path = spec_configurator.configure_from_spec(_SPEC, title="Demo")

    assert (title, env_path) == ("Demo", str(_ENV_PATH))
    site = next(entry for entry in run.asked if entry["label"] == "Demo site")
    assert site["default"] == "demo.example.com"
    assert site["allow_empty"] is False


def test_optional_field_without_a_default_may_be_left_empty(run: _Run) -> None:
    run.answers = {"Demo API key": "key-1"}

    spec_configurator.configure_from_spec(_SPEC, title="Demo")

    note = next(entry for entry in run.asked if entry["label"] == "Demo note")
    assert note["allow_empty"] is True
    assert note["default"] == ""


def test_a_stored_value_is_prefilled_over_the_spec_default(run: _Run) -> None:
    """Re-running onboarding should be a series of enters, not a retype."""
    run.stored = {"api_key": "stored-key", "site": "stored.example.com"}
    run.answers = {}

    spec_configurator.configure_from_spec(_SPEC, title="Demo")

    prefilled = {entry["label"]: entry["default"] for entry in run.asked}
    assert prefilled["Demo API key"] == "stored-key"
    assert prefilled["Demo site"] == "stored.example.com"


def test_a_stored_list_value_is_joined_as_the_prompt_prefill(run: _Run) -> None:
    """Legacy store rows may keep list credentials (e.g. Better Stack sources)."""
    run.stored = {
        "api_key": "stored-key",
        "site": "stored.example.com",
        "note": ["t1_checkout", "t2_api"],
    }
    run.answers = {}

    spec_configurator.configure_from_spec(_SPEC, title="Demo")

    note = next(entry for entry in run.asked if entry["label"] == "Demo note")
    assert note["default"] == "t1_checkout,t2_api"


def test_secret_fields_are_marked_for_masking(run: _Run) -> None:
    run.answers = {"Demo API key": "key-1"}

    spec_configurator.configure_from_spec(_SPEC, title="Demo")

    assert {entry["label"]: entry["secret"] for entry in run.asked} == {
        "Demo API key": True,
        "Demo site": False,
        "Demo note": False,
    }


def test_failed_verification_re_asks_instead_of_leaving_the_wizard(run: _Run) -> None:
    """Onboarding must survive a typo; the user gets another go at the prompts."""
    outcomes = iter([("failed", "Demo rejected the key."), ("passed", "Demo connected.")])
    spec = dataclasses.replace(
        _SPEC, verify=lambda _source, _config: dict(zip(("status", "detail"), next(outcomes)))
    )
    run.answers = {"Demo API key": "key-1"}

    title, _env_path = spec_configurator.configure_from_spec(spec, title="Demo")

    assert title == "Demo"
    # Three fields asked twice: the first round failed, the second succeeded.
    assert len(run.asked) == 6


_MODED_SPEC = setup_flow.IntegrationSetupSpec(
    service="demo-moded",
    # Mode-gated fields are optional at the spec level (as every real moded
    # spec declares them): the un-chosen mode's fields are cleared, not asked.
    fields=(
        setup_flow.SetupField(
            name="token", label="Demo token", env_var="DEMO_TOKEN", required=False
        ),
        setup_flow.SetupField(name="user", label="Demo user", env_var="DEMO_USER", required=False),
        setup_flow.SetupField(
            name="password", label="Demo password", env_var="DEMO_PASSWORD", required=False
        ),
    ),
    mode_prompt="Auth method:",
    modes=(
        setup_flow.SetupMode(
            value="token", label="Token", fields=("token",), required_fields=("token",)
        ),
        setup_flow.SetupMode(value="basic", label="Basic", fields=("user", "password")),
    ),
    verify=lambda _source, _config: {"status": "passed", "detail": "ok"},
)


def test_mode_gate_can_steer_to_a_different_mode_before_fields_are_asked(
    run: _Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vendor may swap the picked mode when its prerequisite is missing."""
    # Arrange — user picks "token"; the gate finds it unusable and steers to "basic"
    monkeypatch.setattr(spec_configurator, "choose", lambda *_a, **_k: "token")
    gated_with: list[str] = []

    def steer_to_basic(mode: str) -> str:
        gated_with.append(mode)
        return "basic"

    run.answers = {"Demo user": "u", "Demo password": "p"}

    # Act
    spec_configurator.configure_from_spec(_MODED_SPEC, title="Demo", on_mode_chosen=steer_to_basic)

    # Assert — the gate saw the picked mode; only the steered mode's fields were asked
    assert gated_with == ["token"]
    asked = [entry["label"] for entry in run.asked]
    assert asked == ["Demo user", "Demo password"]


def test_without_a_mode_gate_the_picked_mode_is_used_unchanged(
    run: _Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setattr(spec_configurator, "choose", lambda *_a, **_k: "token")
    run.answers = {"Demo token": "t"}

    # Act
    spec_configurator.configure_from_spec(_MODED_SPEC, title="Demo")

    # Assert
    assert [entry["label"] for entry in run.asked] == ["Demo token"]


def test_a_field_required_by_the_chosen_mode_may_not_be_left_empty(
    run: _Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional at spec level (unset in other modes), required within this mode.

    AWS's role ARN: blank must be refused at the prompt, not surface later as a
    config-model "requires either role_arn or credentials" validation error.
    """
    # Arrange — user picks "token"; the spec says token is required in that mode
    monkeypatch.setattr(spec_configurator, "choose", lambda *_a, **_k: "token")
    run.answers = {"Demo token": "t"}

    # Act
    spec_configurator.configure_from_spec(_MODED_SPEC, title="Demo")

    # Assert — the token prompt does not allow an empty answer
    token_prompt = next(entry for entry in run.asked if entry["label"] == "Demo token")
    assert token_prompt["allow_empty"] is False


def test_the_same_field_stays_optional_when_another_mode_is_chosen(
    run: _Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — "basic" is chosen; its fields are optional at every level
    monkeypatch.setattr(spec_configurator, "choose", lambda *_a, **_k: "basic")
    run.answers = {"Demo user": "u", "Demo password": "p"}

    # Act
    spec_configurator.configure_from_spec(_MODED_SPEC, title="Demo")

    # Assert
    asked = {entry["label"]: entry["allow_empty"] for entry in run.asked}
    assert asked == {"Demo user": True, "Demo password": True}
