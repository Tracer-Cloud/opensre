"""Prompt fragments for the live task plan.

Renders the per-turn CURRENT PLAN block from the snapshotted plan so
transcript compaction cannot drop it, plus Ask User answered guidance.
"""

from __future__ import annotations

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan
from core.agent_harness.task_plan.progress import format_task_plan_plain

ASK_USER_ANSWERED_GUIDANCE = (
    "ASK USER JUST ANSWERED (this turn). Continue — do not sit idle. "
    "The answer settles the question it belongs to: do not re-ask it, do not ask "
    "what it means, and do not open another round unless the work is impossible "
    "without one more fixed choice. If two rounds "
    "are already answered (see the Q&A above), do NOT ask again — write the "
    "plan now with your best reading of the answers. Two rounds is the hard "
    "maximum.\n"
    "Then update_plan. Put the rationale in explanation=... — the UI renders "
    "it under the checklist. Do not repeat it in the assistant closing reply. "
    "If this is a diagnosis, write structured sections, never one dense "
    "paragraph: Facts; What the signature tells us (what each fact RULES OUT); "
    "Hypothesis ranking with columns # | Hypothesis | Why it fits | "
    "Discriminator. "
    "If this is implementation or plan-only coding work, write a short grounded "
    "rationale (why this sequence, what you will verify, Biggest risk) — do "
    "not invent telemetry or a hypothesis table. "
    "Treat RECENT CONVERSATION as authoritative: preserve the original target "
    "repository and every requested output or metric. The Q&A answers refine "
    "that request; they never replace it. "
    "Answering is the go-ahead to continue the original request. "
    "Do not invent a plan-only pause. Set ask_user_choice(plan_only_after=true) "
    "only when the original request asked not to run yet; then after answers "
    "call update_plan(plan_only=true) and leave every step pending and STOP. "
    "Otherwise set the first step in_progress and execute it now."
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
    "Treat RECENT CONVERSATION as authoritative: preserve the original target "
    "repository and every requested output or metric. The Q&A answers refine "
    "that request; they never replace it. "
    "Do not pass plan_only=false; the host keeps the plan-only latch until the user "
    "confirms a mutating step at the execution gate."
)


def ask_user_answered_block(text: str, *, plan_only: bool = False) -> str:
    """Ephemeral start-now rule when this turn is structured Ask User answers."""
    if not parse_ask_user_answers(text):
        return ""
    if plan_only:
        return ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE
    return ASK_USER_ANSWERED_GUIDANCE


def current_task_plan_block(
    plan: TaskPlan | None,
    *,
    plan_only: bool = False,
) -> str:
    """Render the CURRENT PLAN block, or ``""`` when no plan is attached."""
    if plan is None or not plan.steps:
        return ""
    if plan.all_completed:
        status = "complete"
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
    if plan.all_pending and not plan_only:
        lines.append(
            "Execution is authorized: set the first step to in_progress and "
            "run its tools — do not wait for the user to say go."
        )
    in_progress = next(
        (item.step for item in plan.steps if item.status is PlanStepStatus.IN_PROGRESS),
        None,
    )
    if in_progress is not None:
        lines.append(f"now: {in_progress}")
        lines.append(
            "Do not conclude this turn while a step is in_progress. "
            "Keep working that step, or ask_user_choice if facts are missing. "
            "Do not start another workload."
        )
    elif not plan.all_completed and not plan_only:
        lines.append(
            "Work remains on this plan and no step is in_progress. "
            "Call update_plan to set the next pending step in_progress and "
            "execute it now — do not end the turn idle."
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ASK_USER_ANSWERED_GUIDANCE",
    "ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE",
    "ask_user_answered_block",
    "current_task_plan_block",
]
