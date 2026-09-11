"""Create, revise, and mark steps on the agent's live task plan."""

from __future__ import annotations

from typing import Any

from core.agent_harness.spi.handoff import parse_ask_user_answers
from core.agent_harness.spi.task_plan import (
    TaskPlan,
    apply_update_plan_host_policy,
    apply_update_plan_session,
    demote_unevidenced_completions,
    format_task_plan_plain,
    mark_plan_written,
    parse_task_plan,
    plan_evidence_available,
    task_plan_to_payload,
)
from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from tools.interactive_shell.action_names import ActionToolName

_PLAN_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "step": string_property(
            description="One short observable outcome (about 5–10 words).",
            min_length=1,
        ),
        "status": string_property(
            description="One of: pending, in_progress, completed.",
            enum=("pending", "in_progress", "completed"),
        ),
    },
    "required": ["step", "status"],
    "additionalProperties": False,
}


def execute_update_plan_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    plan, error = parse_task_plan(args)
    if error is not None or plan is None:
        return {"ok": False, "error": error or "invalid plan"}
    turn_text = getattr(ctx, "turn_user_message", "") or ""
    prior = getattr(ctx.session, "task_plan", None)
    if not isinstance(prior, TaskPlan):
        prior = None
    plan, demoted = demote_unevidenced_completions(
        plan,
        prior=prior,
        evidence=plan_evidence_available(ctx.session, prior=prior, turn_user_message=turn_text),
    )
    plan, plan_only_requested = apply_update_plan_host_policy(
        plan,
        plan_only_requested=bool(args.get("plan_only")),
        turn_user_message=turn_text,
        session=ctx.session,
    )
    apply_update_plan_session(ctx.session, plan, plan_only=plan_only_requested)
    mark_plan_written(ctx.session)
    payload = task_plan_to_payload(plan)
    payload["ok"] = True
    payload["summary"] = format_task_plan_plain(plan)
    payload["instruction"] = (
        "Plan stored. Keep it current with update_plan; the CURRENT PLAN "
        "block is the durable record when older messages drop."
    )
    if plan_only_requested:
        payload["instruction"] += " Plan-only: leave every step pending until the user says go."
    elif parse_ask_user_answers(turn_text) and not plan.all_pending:
        payload["instruction"] += (
            " Execution is authorized: the first step is in_progress — run it now."
        )
    elif not plan.all_completed:
        payload["instruction"] += (
            " Continue the in_progress step now — do not end the turn while pending steps remain."
        )
    if demoted:
        names = "; ".join(demoted)
        payload["instruction"] += (
            f" Reset to pending — marked completed before any tool ran for them: {names}."
            " A step is completed only after its work returned while it was in_progress:"
            " set it in_progress, run the work, then mark it completed."
        )
    return payload


def run_update_plan(
    *,
    plan: list[dict[str, Any]] | None = None,
    explanation: str | None = None,
    plan_only: bool = False,
    context: Any,
) -> dict[str, Any]:
    args: dict[str, Any] = {"plan": plan or []}
    if explanation is not None:
        args["explanation"] = explanation
    if plan_only:
        args["plan_only"] = True
    return execute_with_action_context(args, context, execute_update_plan_tool)


update_plan_tool = RegisteredTool(
    name=ActionToolName.UPDATE_PLAN,
    description=(
        "Create or revise the live execution plan for this workload, and mark "
        "steps pending, in_progress, or completed. Call this BEFORE executing "
        "any multi-step workload. Include verification before declaring the task complete. "
        "At most one step may be in_progress. Not for durable human todos "
        "(use work_task_*) and not for /goal keep-going."
    ),
    use_cases=[
        "A multi-step investigation, fix, and verify workload is about to start",
        "A step just finished and the next step is starting",
        "The plan changed and the checklist must be revised",
        "The user asked for a plan only, with no execution yet",
    ],
    anti_examples=[
        "A single obvious lookup or one slash command",
        "Durable human todos / reminders (use work_task_add)",
        "Session-goal keep-going checklists (use session_goal_set)",
    ],
    input_schema=object_schema(
        properties={
            "explanation": string_property(
                description=(
                    "Markdown rationale under the checklist. For incident/"
                    "investigation workloads: Facts, what the signature tells "
                    "us, hypothesis-ranking table. For ordinary implementation "
                    "or plan-only with no incident signals: goal, approach, "
                    "biggest risk — do not invent causal hypotheses. Do not "
                    "repeat in assistant closing prose."
                ),
            ),
            "plan": {
                "type": "array",
                "description": "Ordered steps. At most one status may be in_progress.",
                "items": _PLAN_ITEM_SCHEMA,
                "minItems": 2,
            },
            "plan_only": {
                "type": "boolean",
                "description": (
                    "Set true only when the user's original request asked for "
                    "a plan without running it yet. Do not set this because "
                    "Ask User was answered. Then leave every step pending "
                    "and stop."
                ),
            },
        },
        required=("plan",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_update_plan,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level=SideEffectLevel.READ_ONLY,
)


__all__ = [
    "execute_update_plan_tool",
    "run_update_plan",
    "update_plan_tool",
]
