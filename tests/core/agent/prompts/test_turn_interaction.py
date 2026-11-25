"""Tests for TURN INTERACTION facts injected into the action prompt."""

from __future__ import annotations

from types import SimpleNamespace

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

    monkeypatch.setattr("core.llm.hosted_credits.account_llm_route", object)
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


def _ask_user_answer() -> str:
    from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers

    return format_ask_user_answers(
        (AskUserQuestion(label="", title="Repository?", options=("acme/one",)),), ("acme/one",)
    )


def test_the_tool_call_fact_states_what_the_executor_does_with_several_calls() -> None:
    """The prompt once said a response with several calls runs none; the executor runs them all.

    Pinned against the executor itself, so the fact the model reads cannot
    drift from the runtime again.
    """
    # Arrange
    from core.llm.types import ToolCall
    from core.tool.contracts import RegisteredTool, SideEffectLevel
    from core.tool.execution import execute_tool_calls

    ran: list[str] = []

    def _tool(name: str) -> RegisteredTool:
        return RegisteredTool(
            name=name,
            description="test tool",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            source="knowledge",
            run=lambda: ran.append(name) or {"ok": True},
            side_effect_level=SideEffectLevel.READ_ONLY,
        )

    calls = [ToolCall(id=f"call-{name}", name=name, input={}) for name in ("first", "second")]

    # Act
    results = execute_tool_calls(calls, [_tool("first"), _tool("second")], {})
    text = turn_interaction_facts_block(_snapshot(prompt_surface="interactive_shell"))

    # Assert
    assert ran == ["first", "second"]
    assert not any(result.is_error for result in results)
    assert "several per response all run, none is rejected" in text


@pytest.mark.parametrize(
    ("active_skill", "goal_attached", "message", "guarded"),
    [
        ("repair-github-ci", False, _ask_user_answer(), True),
        # A new request leaves the skill, so nothing requires a plan.
        ("repair-github-ci", False, "How many stars does the repo have?", False),
        (None, False, "How many stars does the repo have?", False),
        # An attached session goal executes directly, inside a skill too.
        ("repair-github-ci", True, _ask_user_answer(), False),
    ],
    ids=["skill_answer", "new_request", "no_skill", "goal_attached"],
)
def test_the_plan_guard_fact_matches_the_guard_the_host_applies(
    active_skill: str | None, goal_attached: bool, message: str, guarded: bool
) -> None:
    """The prompt said the shell refuses every second work tool without a plan.

    The guard applies only inside a skill workflow. The turn's fact and the
    refusal come from one predicate, so the model plans exactly when the host
    would refuse it for not planning.
    """
    # Arrange
    from core.agent_harness.session_goal.goal import SessionGoal
    from core.agent_harness.task_plan.required import plan_required
    from core.agent_harness.turns.skill_activation import prepare_active_skill
    from surfaces.interactive_shell.session import Session

    session = Session(active_skill=active_skill)
    if goal_attached:
        session.session_goal = SessionGoal(condition="Check five PRs")

    # Act: the snapshot is taken first, then the driver settles the active skill.
    snapshot = TurnSnapshot.from_session(message, session, surface="interactive_shell")
    text = turn_interaction_facts_block(snapshot)
    prepare_active_skill(session, message)
    refused = plan_required(
        session, tool_name="shell_run", arguments={}, is_action=True, work_in_flight=1
    )

    # Assert
    assert snapshot.plan_guard_active is guarded
    assert refused is guarded
    assert ("store a plan before the second work tool" in text) is guarded
    assert ("No stored plan is required unless a skill is loaded" in text) is not guarded


def test_a_skill_loaded_mid_turn_is_covered_by_the_plan_guard_fact() -> None:
    """The turn's fact must stay true after ``skill_view`` activates a skill.

    The turn starts outside any skill, so the model reads that no plan is
    required; the guard reads the live session, and refuses the second work
    call once a skill has loaded. The fact names that exception, so the model
    is not promised something the host then refuses.
    """
    # Arrange: an ordinary request, with the hooks the driver installs.
    from core.agent_harness.task_plan.required import PLAN_REQUIRED_REASON
    from core.agent_harness.turns.plan_hooks import with_task_plan_hooks
    from core.domain.types.tools import ToolRole
    from core.llm.types import ToolCall
    from core.tool.execution import ToolExecutionRequest, ToolExecutionResult
    from surfaces.interactive_shell.session import Session

    def request(name: str) -> ToolExecutionRequest:
        return ToolExecutionRequest(
            tool_call=ToolCall(id=f"call-{name}", name=name, input={}),
            tool=SimpleNamespace(role=ToolRole.ACTION),  # type: ignore[arg-type]
            arguments={},
            source="test",
            resolved_integrations={},
        )

    session = Session()
    message = "Why is CI failing on the repo?"
    text = turn_interaction_facts_block(
        TurnSnapshot.from_session(message, session, surface="interactive_shell")
    )
    hooks = with_task_plan_hooks(None, session)

    # Act: the model loads a skill, then makes two work calls with no plan.
    session.active_skill = "repair-github-ci"
    first = hooks.before_tool_call(request("shell_run"))
    hooks.after_tool_call(
        request("shell_run"), ToolExecutionResult(content="out", details={"ok": True})
    )
    second = hooks.before_tool_call(request("shell_run"))

    # Assert
    assert "unless a skill is loaded" in text
    assert first is None
    assert second is not None and second.blocked is True
    assert second.reason == PLAN_REQUIRED_REASON
