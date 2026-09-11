"""Planning instructions and CURRENT PLAN prompt injection."""

from __future__ import annotations

from core.agent_harness.prompts import (
    PromptBlockId,
    PromptTier,
    build_action_system_prompt_envelope,
)
from core.agent_harness.task_plan.plan import parse_task_plan
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _ctx(*, plan=None) -> TurnSnapshot:
    return TurnSnapshot(
        text="investigate checkout 502s",
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
        task_plan=plan,
    )


def test_composed_prompt_omits_removed_planning_instructions_file() -> None:
    from core.agent_harness.prompts import build_action_system_prompt

    prompt = build_action_system_prompt(_ctx())
    assert "PLANNING — update_plan" not in prompt
    assert "ASK THEN PLAN" not in prompt
    assert "action-agent-planning-instructions" not in [
        block.id for block in build_action_system_prompt_envelope(_ctx()).blocks
    ]


def test_current_plan_is_ephemeral_so_compaction_cannot_drop_it() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Run /health and read the result", "status": "completed"},
                {"step": "List connected integrations", "status": "in_progress"},
                {"step": "Confirm both outputs answered the ask", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    envelope = build_action_system_prompt_envelope(_ctx(plan=plan))
    block = envelope.require_block(PromptBlockId.CURRENT_TASK_PLAN)
    assert block.tier == PromptTier.EPHEMERAL
    assert "Plan · 2/3" in block.content
    assert "CURRENT PLAN" in block.content
    assert "Do not conclude this turn while a step is in_progress" in block.content
    cached = envelope.render_cached()
    assert "Plan · 2/3" not in cached
    assert "Plan · 2/3" in envelope.render()


def test_ask_user_answers_inject_start_now_block() -> None:
    from core.agent_harness.session.pending_choice import (
        AskUserQuestion,
        format_ask_user_answers,
    )
    from core.agent_harness.task_plan.prompt import ASK_USER_ANSWERED_GUIDANCE

    answers = format_ask_user_answers(
        (
            AskUserQuestion(label="Env", title="Where is it?", options=("Prod", "Dev")),
            AskUserQuestion(label="Window", title="What window?", options=("24h", "7d")),
        ),
        ("Dev", "24h"),
    )
    snapshot = TurnSnapshot(
        text=answers,
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
    )
    envelope = build_action_system_prompt_envelope(snapshot)
    block = envelope.require_block(PromptBlockId.ASK_USER_ANSWERED)
    assert block.tier == PromptTier.EPHEMERAL
    assert ASK_USER_ANSWERED_GUIDANCE in block.content
    assert ASK_USER_ANSWERED_GUIDANCE not in envelope.render_cached()
    assert ASK_USER_ANSWERED_GUIDANCE in envelope.render()
    assert envelope.block(PromptBlockId.ASK_USER_ANSWERED) is not None
    idle = build_action_system_prompt_envelope(_ctx())
    assert idle.block(PromptBlockId.ASK_USER_ANSWERED) is None


def test_ask_user_answered_guidance_defaults_to_execute_not_pause() -> None:
    from core.agent_harness.task_plan.prompt import ASK_USER_ANSWERED_GUIDANCE

    text = ASK_USER_ANSWERED_GUIDANCE.lower()
    assert "go-ahead to continue" in text
    assert "do not invent a plan-only pause" in text
    assert "plan_only_after=true" in text
    assert "in_progress and execute it now" in text


def test_ask_user_answered_guidance_scopes_diagnosis_shape_to_incidents() -> None:
    from core.agent_harness.task_plan.prompt import (
        ASK_USER_ANSWERED_GUIDANCE,
        ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE,
    )

    for text in (ASK_USER_ANSWERED_GUIDANCE, ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE):
        assert "If this is a diagnosis" in text
        assert "If this is implementation or plan-only coding work" in text
        assert "do not invent telemetry" in text.lower()


def test_ask_user_answers_preserve_original_repo_and_all_requested_metrics() -> None:
    from core.agent_harness.session.pending_choice import (
        AskUserQuestion,
        format_ask_user_answers,
    )

    original = "For facebook/react, return merged PR count, median time-to-merge, and star gain."
    answers = format_ask_user_answers(
        (AskUserQuestion(label="Window", title="Which date window?", options=("7d", "30d")),),
        ("7d",),
    )
    snapshot = TurnSnapshot(
        text=answers,
        conversation_messages=(("user", original),),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
    )

    rendered = build_action_system_prompt_envelope(snapshot).render()

    assert original in rendered
    assert "preserve the original target repository" in rendered
    assert "every requested output or metric" in rendered
    assert "Q&A answers refine that request; they never replace it" in rendered


def test_ask_user_answered_plan_only_guidance_does_not_authorize_execute() -> None:
    from core.agent_harness.session.pending_choice import (
        AskUserQuestion,
        format_ask_user_answers,
    )
    from core.agent_harness.task_plan.prompt import ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE

    answers = format_ask_user_answers(
        (
            AskUserQuestion(label="Env", title="Where is it?", options=("Prod", "Dev")),
            AskUserQuestion(label="Window", title="What window?", options=("24h", "7d")),
        ),
        ("Dev", "24h"),
    )
    snapshot = TurnSnapshot(
        text=answers,
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
        plan_only_until_authorized=True,
    )
    envelope = build_action_system_prompt_envelope(snapshot)
    block = envelope.require_block(PromptBlockId.ASK_USER_ANSWERED)
    assert ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE in block.content
    assert "do not pass plan_only=false" in block.content.lower()
    assert "in_progress and execute it now" not in block.content.lower()


def test_current_task_plan_block_is_empty_without_steps() -> None:
    from core.agent_harness.task_plan.prompt import current_task_plan_block

    assert current_task_plan_block(None) == ""
    empty, error = parse_task_plan({"plan": [{"step": "x", "status": "pending"}]})
    assert error is not None
    assert current_task_plan_block(empty) == ""


def test_current_task_plan_block_plan_only_does_not_authorize_execution() -> None:
    from core.agent_harness.task_plan.prompt import current_task_plan_block

    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect the failing job", "status": "pending"},
                {"step": "Confirm the workflow is green", "status": "pending"},
            ],
            "explanation": "do not run yet",
        }
    )
    assert error is None and plan is not None
    block = current_task_plan_block(plan, plan_only=True)
    assert "CURRENT PLAN (ready, nothing executed" in block
    assert "explanation: do not run yet" in block
    assert "Execution is authorized" not in block


def test_current_task_plan_block_all_pending_without_latch_authorizes() -> None:
    from core.agent_harness.task_plan.prompt import current_task_plan_block

    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect the failing job", "status": "pending"},
                {"step": "Confirm the workflow is green", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    block = current_task_plan_block(plan, plan_only=False)
    assert "Execution is authorized" in block


def test_current_task_plan_block_completed_status() -> None:
    from core.agent_harness.task_plan.prompt import current_task_plan_block

    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect the failing job", "status": "completed"},
                {"step": "Confirm the workflow is green", "status": "completed"},
            ]
        }
    )
    assert error is None and plan is not None
    block = current_task_plan_block(plan)
    assert "CURRENT PLAN (complete" in block
    assert "in_progress" not in block


def test_current_task_plan_block_defers_to_the_latest_message() -> None:
    """A plan from an earlier turn yields to a new question or a skill branch."""
    from core.agent_harness.task_plan.prompt import (
        PLAN_PRECEDENCE_RULE,
        current_task_plan_block,
    )

    # Arrange: an unfinished plan with a step still in progress.
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Load the skill", "status": "completed"},
                {"step": "Confirm the repository", "status": "in_progress"},
                {"step": "Run the analysis", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None

    # Act
    block = current_task_plan_block(plan)

    # Assert: precedence rule present; the keep-working rule is scoped to
    # continuation turns.
    assert PLAN_PRECEDENCE_RULE in block
    assert "latest message decides" in block
    assert "When this turn continues the plan: Do not conclude" in block


def test_skill_answer_turn_omits_the_generic_answered_guidance() -> None:
    """Inside a skill the skill's own rules govern the answer turn."""
    from core.agent_harness.session.pending_choice import (
        AskUserQuestion,
        format_ask_user_answers,
    )

    # Arrange: an Ask User answer while a skill is active.
    answers = format_ask_user_answers(
        (AskUserQuestion(label="Next", title="What next?", options=("Schedule", "Exit")),),
        ("Schedule",),
    )
    snapshot = TurnSnapshot(
        text=answers,
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
        active_skill="analyzing-github-ci-performance",
    )

    # Act
    envelope = build_action_system_prompt_envelope(snapshot)

    # Assert: no generic plan-and-execute block; the skill block carries precedence.
    assert envelope.block(PromptBlockId.ASK_USER_ANSWERED) is None
    skill_block = envelope.require_block(PromptBlockId.ACTIVE_SKILL)
    assert "The skill decides the next tool call" in skill_block.content
