"""Live agent task plan (``update_plan``) — not ``/goal`` and not ``/work``.

Surfaces and the ``update_plan`` tool import curated names from
``core.agent_harness.spi.task_plan``. Import a leaf for a rule; this
``__init__`` must not become a second SPI.

Leaves:

* :mod:`plan` — ``TaskPlan``, parse/validate, item JSON schema
* :mod:`completion` — which completions this write has earned
* :mod:`write_result` — model-facing ``update_plan`` instruction
* :mod:`evidence` — work-return counters for a turn
* :mod:`required` — second work tool needs an open plan
* :mod:`conclusion` — whether the plan still blocks ending the turn
* :mod:`update_plan_policy` — Ask User / plan-only latch
* :mod:`persist` — flush / restore
* :mod:`progress` — plain-text ``Plan · n/m`` checklist
* :mod:`prompt` — planning instructions + CURRENT PLAN block
* :mod:`work_log` — host records per-step work for the post-execution breakdown
"""

from __future__ import annotations

from core.agent_harness.task_plan.discard import discard_task_plan
from core.agent_harness.task_plan.plan import (
    PlanStep,
    PlanStepStatus,
    TaskPlan,
    parse_task_plan,
    task_plan_from_payload,
    task_plan_to_payload,
)
from core.agent_harness.task_plan.progress import (
    PLAN_STATUS_GLYPH,
    format_plan_header,
    format_task_plan_plain,
)

__all__ = [
    "PLAN_STATUS_GLYPH",
    "PlanStep",
    "PlanStepStatus",
    "TaskPlan",
    "discard_task_plan",
    "format_plan_header",
    "format_task_plan_plain",
    "parse_task_plan",
    "task_plan_from_payload",
    "task_plan_to_payload",
]
