"""Both speakers that can answer the user see the same authoritative facts.

The action agent can end a turn in visible prose, so it is a user-facing
speaker. It was the only one built without runtime facts: the assistant prompt
carries the host/cloud/version block and the action prompt carried none. A
speaker asked to answer without the facts invents them — a laptop turn reported
running in AWS, and a Slack turn denied being the bot it was addressed as.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.prompts.action import build_action_system_prompt
from core.agent_harness.turns.turn_snapshot import TurnSnapshot

_FACT_MARKERS = (
    "host operating system is ",
    "OpenSRE version is ",
)


def _snapshot(**overrides: Any) -> TurnSnapshot:
    fields: dict[str, Any] = {
        "text": "what environment are you running in?",
        "conversation_messages": (),
        "configured_integrations": (),
        "configured_integrations_known": True,
        "reasoning_effort": None,
        "setup_state": "",
    }
    fields.update(overrides)
    return TurnSnapshot(**fields)


def test_the_action_prompt_states_the_same_runtime_facts_as_the_assistant() -> None:
    """A user-facing speaker must not have to guess the host it runs on."""
    # Act
    prompt = build_action_system_prompt(_snapshot())

    # Assert
    missing = [marker for marker in _FACT_MARKERS if marker not in prompt]
    assert missing == [], (
        f"the action prompt omits authoritative runtime facts: {missing}. "
        "The action agent can answer the user directly, so it needs the same "
        "block the assistant gets or it will invent what it was not told."
    )


def test_the_action_prompt_states_cloud_absence_rather_than_leaving_it_open() -> None:
    """Silence about the cloud is what the AWS answer filled in."""
    prompt = build_action_system_prompt(_snapshot())

    assert "cloud" in prompt.lower()
