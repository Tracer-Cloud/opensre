"""The action planner sees the operator's setup, not just connected integrations."""

from __future__ import annotations

from core.agent_harness.prompts.action.assemble import build_action_system_prompt_envelope
from core.agent_harness.prompts.kernel.envelope import PromptTier
from core.agent_harness.turns.turn_snapshot import TurnSnapshot

_BLOCK_ID = "action-agent-setup-state"
_MARKER = "Scheduled tasks: 23 configured, 23 able to deliver"


def _snapshot(setup_state: str) -> TurnSnapshot:
    return TurnSnapshot(
        text="set up a morning report",
        conversation_messages=(),
        configured_integrations=("slack",),
        configured_integrations_known=True,
        reasoning_effort=None,
        setup_state=setup_state,
    )


def test_planner_prompt_carries_setup_state() -> None:
    # Arrange / Act
    envelope = build_action_system_prompt_envelope(_snapshot(f"{_MARKER}\n"))

    # Assert: the planner decides whether to offer a schedule, so it has to know
    # whether any already exist.
    assert _MARKER in envelope.render()


def test_setup_state_sits_in_the_context_tier() -> None:
    # Arrange / Act
    envelope = build_action_system_prompt_envelope(_snapshot(f"{_MARKER}\n"))

    # Assert
    block = next(b for b in envelope.blocks if b.id == _BLOCK_ID)
    assert block.tier is PromptTier.CONTEXT


def test_absent_setup_state_adds_no_block() -> None:
    # Arrange / Act
    envelope = build_action_system_prompt_envelope(_snapshot(""))

    # Assert: an empty header would read as "nothing configured".
    assert all(block.id != _BLOCK_ID for block in envelope.blocks)


def test_gateway_surface_omits_setup_state_from_snapshot() -> None:
    # Arrange: shared chat must not broadcast install schedules/integrations.
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

    class _Session:
        cli_agent_messages: list[tuple[str, str]] = []
        configured_integrations = ("slack",)
        configured_integrations_known = True
        reasoning_effort = None

    # Act
    snap = TurnSnapshot.from_session("hi", _Session(), surface="gateway")

    # Assert
    assert snap.setup_state == ""


def test_unknown_surface_omits_install_facts() -> None:
    # Arrange: a caller that cannot say which surface the turn came from. The
    # gate on profile_for fails OPEN by design (an unknown surface still gets
    # the full prompt), which is the wrong direction for install facts.
    from core.agent_harness.turns.turn_snapshot import _setup_state_for_surface

    # Act
    rendered = _setup_state_for_surface(("slack",), None)

    # Assert: silence beats broadcasting one operator's setup into an unknown
    # destination.
    assert rendered == ""


def test_named_shell_surface_still_gets_the_facts() -> None:
    # Arrange / Act: the known-good case must keep working.
    from core.agent_harness.turns.turn_snapshot import _setup_state_for_surface

    rendered = _setup_state_for_surface(("slack",), "interactive_shell")

    # Assert
    assert "Setup state" in rendered
