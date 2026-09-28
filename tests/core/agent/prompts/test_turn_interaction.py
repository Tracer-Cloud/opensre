"""Tests for TURN INTERACTION facts injected into the action prompt."""

from __future__ import annotations

import pytest

from config.account_credits import AccountCredits, HostedCreditsKindValue, HostedCreditsRead
from core.agent_harness.prompts import build_action_system_prompt_envelope
from core.agent_harness.prompts.action.turn_interaction import turn_interaction_facts_block
from core.agent_harness.prompts.kernel.envelope import PromptBlockId
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _snapshot(**overrides: object) -> TurnSnapshot:
    fields: dict[str, object] = {
        "text": "ship it",
        "conversation_messages": (),
        "configured_integrations": (),
        "configured_integrations_known": True,
        "reasoning_effort": None,
    }
    fields.update(overrides)
    return TurnSnapshot(**fields)  # type: ignore[arg-type]


def test_turn_interaction_facts_block_names_surface_goal_and_menu() -> None:
    text = turn_interaction_facts_block(
        _snapshot(
            prompt_surface="gateway",
            session_goal_attached=True,
            interactive_choice_available=False,
        )
    )
    assert "surface: gateway" in text
    assert "session_goal: attached" in text
    assert "ask_user_choice menu: unavailable" in text
    assert "only when the menu is available AND session_goal is none" in text


def test_turn_interaction_facts_include_hosted_credits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _hosted_read() -> HostedCreditsRead:
        return HostedCreditsRead(
            HostedCreditsKindValue.OK,
            AccountCredits(
                total=12_500,
                monthly=12_500,
                monthly_limit=100_000,
                top_up=0,
                resets_at=None,
                plan_id="team",
            ),
            "OpenSRE hosted credits.",
        )

    monkeypatch.setattr("core.llm.hosted_credits.account_llm_route", lambda: object())
    monkeypatch.setattr("core.llm.hosted_credits.cached_hosted_credits", _hosted_read)
    text = turn_interaction_facts_block(_snapshot(prompt_surface="interactive_shell"))
    assert "OpenSRE hosted credits remaining are 12,500" in text
    assert "hosted LLM requests are allowed" in text


def test_action_envelope_includes_turn_interaction_block() -> None:
    envelope = build_action_system_prompt_envelope(
        _snapshot(
            prompt_surface="interactive_shell",
            interactive_choice_available=True,
        )
    )
    block = envelope.require_block(PromptBlockId.TURN_INTERACTION)
    assert "surface: interactive_shell" in block.content
    assert "ask_user_choice menu: available" in block.content


def test_headless_cli_facts_forbid_interactive_shell_claims() -> None:
    text = turn_interaction_facts_block(
        _snapshot(
            prompt_surface="headless_cli",
            interactive_choice_available=True,
        )
    )

    assert "surface: headless_cli" in text
    assert "non-interactive" in text
    assert "do not describe it as the interactive shell" in text
    assert "do not recommend slash commands" in text


def test_ephemeral_headless_cli_facts_do_not_advertise_resumability() -> None:
    text = turn_interaction_facts_block(
        _snapshot(
            prompt_surface="headless_cli",
            interactive_choice_available=False,
        )
    )

    assert "non-resumable" in text
    assert "or call ask_user_choice" in text
    assert "use a required structured choice" not in text


def test_turn_interaction_block_carries_the_goal_brief_when_a_goal_is_attached() -> None:
    # Arrange: the snapshot carries the goal brief the way from_session builds it.
    from core.agent_harness.session_goal.goal import SessionGoal
    from core.agent_harness.session_goal.progress import format_session_goal_brief

    goal = SessionGoal(
        condition="Check five PRs",
        checklist=("list PRs", "check runs"),
        completed=frozenset({0}),
        last_reason="not yet — check runs by SHA",
    )

    # Act
    text = turn_interaction_facts_block(
        _snapshot(
            prompt_surface="interactive_shell",
            session_goal_attached=True,
            session_goal_brief=format_session_goal_brief(goal),
        )
    )

    # Assert: the first goal turn sees the condition, the ticks, and the tick tool.
    assert "condition: Check five PRs" in text
    assert "[x] 0. list PRs" in text
    assert "[ ] 1. check runs" in text
    assert "session_goal_complete" in text
    assert "last verdict: not yet — check runs by SHA" in text


def test_an_unattended_gateway_turn_is_told_to_ask_once_and_a_chat_gateway_turn_is_not() -> None:
    # Arrange
    from core.agent_harness.turns.turn_snapshot import _interactive_choice_available

    class _Session:
        def __init__(self, capabilities: dict[str, tuple[str, ...]]) -> None:
            self.available_capabilities = capabilities

    unattended = _Session({"ask_user_choice": ("deferred",)})
    chat = _Session({})

    # Act
    unattended_available = _interactive_choice_available(unattended, "gateway")
    chat_available = _interactive_choice_available(chat, "gateway")
    text = turn_interaction_facts_block(
        _snapshot(prompt_surface="gateway", interactive_choice_available=True)
    )

    # Assert: only the unattended prompt may park a choice, and its rule says to ask once
    assert unattended_available is True and chat_available is False
    assert "unattended prompt on the hosted gateway" in text
    assert "call ask_user_choice once" in text
