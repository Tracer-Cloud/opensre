"""Task-plan types a host renders: the live checklist model and its parser.

A surface reads a :class:`TaskPlan` off the session to draw the ``Plan · n/m``
checklist and parses an ``update_plan`` payload through :func:`parse_task_plan`.
"""

from __future__ import annotations

from core.agent_harness.task_plan.display import (
    ensure_active_step,
    is_plan_diagnosis_prose,
    promote_first_pending_step,
)
from core.agent_harness.task_plan.evidence import (
    mark_plan_written,
    plan_evidence_available,
    record_plan_evidence,
)
from core.agent_harness.task_plan.plan import (
    PlanStep,
    PlanStepStatus,
    TaskPlan,
    parse_task_plan,
    task_plan_to_payload,
)
from core.agent_harness.task_plan.progress import (
    PLAN_STATUS_GLYPH,
    format_plan_header,
    format_task_plan_plain,
)
from core.agent_harness.task_plan.update_plan_policy import (
    apply_update_plan_host_policy,
    apply_update_plan_session,
    demote_unevidenced_completions,
)
from core.agent_harness.task_plan.work_log import (
    record_task_plan_work,
    take_completed_plan_breakdown,
)

__all__ = [
    "PLAN_STATUS_GLYPH",
    "PlanStep",
    "PlanStepStatus",
    "TaskPlan",
    "apply_update_plan_host_policy",
    "apply_update_plan_session",
    "demote_unevidenced_completions",
    "ensure_active_step",
    "format_plan_header",
    "format_task_plan_plain",
    "is_plan_diagnosis_prose",
    "mark_plan_written",
    "parse_task_plan",
    "plan_evidence_available",
    "promote_first_pending_step",
    "record_plan_evidence",
    "record_task_plan_work",
    "take_completed_plan_breakdown",
    "task_plan_to_payload",
]
