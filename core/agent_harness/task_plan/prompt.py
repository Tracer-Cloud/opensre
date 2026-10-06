"""Prompt fragments for the live task plan.

Renders the per-turn CURRENT PLAN block from the snapshotted plan so
transcript compaction cannot drop it, plus Ask User answered guidance.
"""

from __future__ import annotations

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan
from core.agent_harness.task_plan.progress import format_task_plan_plain

_ANSWER_SETTLES = (
    "The answer settles the question it belongs to: do not re-ask it, do not ask "
    "what it means, and do not open another round unless the work is impossible "
    "without one more fixed choice. If two rounds "
    "are already answered (see the Q&A above), do NOT ask again — "
)
_ANSWER_KEEPS_REQUEST = (
    "Treat the earlier conversation as authoritative: preserve the original target "
    "repository and every requested output or metric. The Q&A answers refine "
    "that request; they never replace it. "
    "Answering is the go-ahead to continue the original request. "
    "Do not invent a plan-only pause. "
)

ASK_USER_ANSWERED_GUIDANCE = (
    "ASK USER JUST ANSWERED (this turn). Continue — do not sit idle. "
    + _ANSWER_SETTLES
    + "write the plan now with your best reading of the answers. Two rounds is the hard "
    "maximum.\n"
    "Then update_plan. Put the rationale "
    "in explanation=... — the UI renders it under the checklist. Do not repeat "
    "it in the assistant closing reply. "
    "If this is a diagnosis, write structured sections, never one dense "
    "paragraph: Facts; What the signature tells us (what each fact RULES OUT); "
    "Hypothesis ranking with columns # | Hypothesis | Why it fits | "
    "Discriminator. "
    "If this is implementation or plan-only coding work, write a short grounded "
    "rationale (why this sequence, what you will verify, Biggest risk) — do "
    "not invent telemetry or a hypothesis table. "
    + _ANSWER_KEEPS_REQUEST
    + "Set ask_user_choice(plan_only_after=true) "
    "only when the original request asked not to run yet; then after answers "
    "call update_plan(plan_only=true) and leave every step pending and STOP. "
    "Otherwise set the first step in_progress in the same response as its tool "
    "and run that tool. A response that only updates the plan spends a model "
    "call without progress."
)

#: The answer continues the open plan's own workflow: the host advances it.
ASK_USER_ANSWERED_CONTINUES_PLAN_GUIDANCE = (
    "ASK USER JUST ANSWERED (this turn). Continue — do not sit idle. "
    + _ANSWER_SETTLES
    + "continue with your best reading of the answers. Two rounds is the hard "
    "maximum.\n"
    "The answer continues the CURRENT PLAN: run the next step's tool now. The "
    "host marks the step the answer settled completed and starts the next one "
    "when that tool is called. Call update_plan only to revise the plan (put the "
    "rationale in explanation=...), mark a step blocked, or settle it — not to "
    "mark progress. "
    + _ANSWER_KEEPS_REQUEST
    + "A response that only updates the plan spends a model call without progress."
)

ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE = (
    "ASK USER JUST ANSWERED (this turn). This request is plan-only — answering "
    "does not authorize execution. The answer settles the question it belongs "
    "to: do not re-ask it or ask what it means; open another round only when the "
    "plan is impossible without one more fixed choice. If two rounds are already "
    "answered, do NOT ask again. "
    "Then update_plan with every step pending and STOP. Put the rationale in "
    "explanation=... "
    "If this is a diagnosis: Facts; What the signature tells us (what each "
    "fact RULES OUT); Hypothesis ranking with Discriminator. "
    "If this is implementation or plan-only coding work: why this sequence, "
    "what you will verify, Biggest risk — do not invent telemetry or a "
    "hypothesis table. "
    "Treat the earlier conversation as authoritative: preserve the original target "
    "repository and every requested output or metric. The Q&A answers refine "
    "that request; they never replace it. "
    "Do not pass plan_only=false; the host keeps the plan-only latch until the user "
    "confirms a mutating step at the execution gate."
)


PLAN_PRECEDENCE_RULE = (
    "This plan was made in an earlier turn. The user's latest message decides "
    "what this turn does. If it continues the plan (a plan continuation, a "
    "go-ahead, or an answer to this plan's own question), work the next step. "
    "If it asks for something else — a question, a remark, a new request — "
    "answer that and leave the plan as it is; do not resume it unasked and do "
    "not announce that you will continue it. Inside an ACTIVE SKILL, the "
    "skill's branch for the answer is the next step: update the plan to match "
    "the skill instead of replaying steps the plan still lists."
)


def ask_user_answered_block(
    text: str, *, plan_only: bool = False, continues_plan: bool = False
) -> str:
    """Ephemeral start-now rule when this turn is structured Ask User answers.

    ``continues_plan`` says the answer belongs to the open plan's own
    workflow, so the host advances the plan and no status write is asked for.
    """
    if not parse_ask_user_answers(text):
        return ""
    if plan_only:
        return ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE
    if continues_plan:
        return ASK_USER_ANSWERED_CONTINUES_PLAN_GUIDANCE
    return ASK_USER_ANSWERED_GUIDANCE


def current_task_plan_block(
    plan: TaskPlan | None,
    *,
    plan_only: bool = False,
    host_advances: bool = False,
) -> str:
    """Render the CURRENT PLAN block, or ``""`` when no plan is attached.

    ``host_advances`` says this turn answers the plan owner's question, so the
    host moves the plan on the next step's tool; otherwise the model is told
    to pair each status update with the step's tool.
    """
    if plan is None or not plan.steps:
        return ""
    if plan.all_completed:
        status = "complete"
    elif plan.is_settled:
        status = f"ended; {plan.blocked_count} blocked, nothing left to run"
    elif plan.all_pending:
        status = "ready, nothing executed"
    else:
        status = "in progress"
    lines = [
        f"CURRENT PLAN ({status}; Plan · {plan.current_index}/{plan.total}). "
        "This is the durable record — older messages may have dropped an "
        "earlier version. Keep it current with update_plan; do not recreate "
        "it from memory.",
        format_task_plan_plain(plan),
    ]
    if plan.explanation:
        lines.append(f"explanation: {plan.explanation}")
    lines.append(PLAN_PRECEDENCE_RULE)
    if plan.all_pending and not plan_only:
        lines.append(
            "Execution is authorized: set the first step in_progress in the "
            "same response as its tool and run that tool. A response that only "
            "updates the plan spends a model call without progress. Do not wait "
            "for the user to say go."
        )
    in_progress = next(
        (item.step for item in plan.steps if item.status is PlanStepStatus.IN_PROGRESS),
        None,
    )
    if in_progress is not None:
        lines.append(f"now: {in_progress}")
        lines.append(
            "When this turn continues the plan: Do not conclude this turn while "
            "a step is in_progress. Keep working that step, or ask_user_choice "
            "if facts are missing. Do not start another workload."
        )
        if host_advances and not plan_only:
            lines.append(
                "The host completes this step and starts the next when you call "
                "the next step's tool; do not send update_plan just to mark "
                "progress. A response that only updates the plan spends a model "
                "call without progress."
            )
        elif not plan_only:
            lines.append(
                "Send the status update in the same response as the step's tool. "
                "A response that only updates the plan spends a model call "
                "without progress."
            )
    elif not plan.is_settled and not plan_only:
        lines.append(
            "When this turn continues the plan: Work remains on this plan and "
            "no step is in_progress. Set the next pending step in_progress in "
            "the same response as its tool and run that tool. A response that "
            "only updates the plan spends a model call without progress."
        )
    if plan.blocked_count:
        lines.append(
            "Blocked steps stay blocked: their work did not happen. Do not run "
            "tools to earn a completed mark for them, and name each blocker "
            "when you report."
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ASK_USER_ANSWERED_CONTINUES_PLAN_GUIDANCE",
    "ASK_USER_ANSWERED_GUIDANCE",
    "ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE",
    "PLAN_PRECEDENCE_RULE",
    "ask_user_answered_block",
    "current_task_plan_block",
]
