"""Evidence mappers for work_task_list and work_task_prioritize."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry, unique_evidence_source
from infrastructure.text.truncation import truncate

#: Bound a task title echoed into a report summary.
_TITLE_SUMMARY_TRUNCATE_LEN = 60


def map_work_task_list(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the task count and status filter.

    ``total`` is the true count of matching items *before* the caller's
    ``limit`` slice, so it needs no "N+" qualifier -- unlike ``returned``,
    which is the (possibly smaller) page actually included in ``tasks``.
    """
    if output.get("error"):
        return
    total = output.get("total", 0)
    if not total:
        return
    returned = output.get("returned", total)
    status = tool_input.get("status", "open")
    summary = f"{total} task(s)"
    if status:
        summary += f" with status '{status}'"
    if returned < total:
        summary += f" ({returned} shown)"
    record_evidence_entry(
        evidence,
        source=unique_evidence_source(evidence, "work_task_list"),
        label="Work Tasks",
        summary=summary,
    )


def map_work_task_prioritize(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite how many tasks were ranked and the top recommendation."""
    recommendations = output.get("recommendations") or []
    if not recommendations:
        return
    top = recommendations[0]
    top_task = top.get("task") if isinstance(top, dict) else None
    top_title = (top_task or {}).get("title") if isinstance(top_task, dict) else None
    summary = f"{len(recommendations)} task(s) ranked"
    if top_title:
        summary += f", top: '{truncate(str(top_title), _TITLE_SUMMARY_TRUNCATE_LEN)}'"
    record_evidence_entry(
        evidence,
        source=unique_evidence_source(evidence, "work_task_prioritize"),
        label="Work Task Priorities",
        summary=summary,
    )
