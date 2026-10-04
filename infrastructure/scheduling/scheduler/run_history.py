"""Record what each scheduled run attempt ran and how it ended.

A record holds the task as saved when the attempt was claimed, the exact
messages its turns submitted, the report it built, and every delivery outcome,
so a later edit to the loop never changes an earlier record. It is written to
the run-record file when an attempt starts and replaced when it ends, from the
run row the attempt owns, so a stale worker only ever writes its own attempt.
When an attempt ends, the record is also sent as ``scheduled_task_run_recorded``,
because dashboards read analytics events and never gateway files.

Text is credential-redacted and capped. Recording never fails a run.
"""

from __future__ import annotations

import logging
from typing import Any

from infrastructure.observability.trace.submitted_messages import SubmittedMessages
from infrastructure.safety.secret_redaction import redact_text
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_GROUP_ID_PARAM,
    LOOP_REPORT_ARGS_PARAM,
    LOOP_REPORT_PARAM,
)
from infrastructure.scheduling.scheduler.registry_telemetry import registry_entry
from infrastructure.scheduling.scheduler.storage.run_record_store import save_run_record
from infrastructure.scheduling.scheduler.storage.run_store import ExecutionClaim, get_claim_run
from infrastructure.scheduling.scheduler.types import ScheduledTask, TaskRun, TaskStatus

logger = logging.getLogger(__name__)

RUN_RECORD_VERSION = 1
#: Caps keep one event far below the 256 KiB analytics payload limit.
_PROMPT_MAX_CHARS = 16_000
_PROMPTS_KEPT = 3
_REPORT_MAX_CHARS = 20_000
_SUMMARY_MAX_CHARS = 500
_ERROR_MAX_CHARS = 300
_BUILDER_ARGS_MAX_CHARS = 500
_DELIVERY_KEPT = 10
_FIELD_MAX_CHARS = 200


def _bounded(value: str, max_chars: int) -> tuple[str, bool]:
    text = redact_text(value)
    if len(text) <= max_chars:
        return text, False
    return text[: max_chars - 1].rstrip() + "…", True


def _trigger(fire_time: str) -> str:
    """Scheduled fires use second precision; a manual run's key carries microseconds."""
    return "manual" if "." in fire_time else "schedule"


def _report_builder(task: ScheduledTask) -> dict[str, str] | None:
    """The deterministic builder a loop names; it submits no model message."""
    name = task.params.get(LOOP_REPORT_PARAM, "").strip()
    if not name:
        return None
    args = _bounded(task.params.get(LOOP_REPORT_ARGS_PARAM, "").strip(), _BUILDER_ARGS_MAX_CHARS)[0]
    return {"name": name[:_FIELD_MAX_CHARS], "args": args}


def _submitted_fields(submitted: SubmittedMessages) -> dict[str, Any]:
    prompts = []
    for message in submitted.messages[:_PROMPTS_KEPT]:
        text, truncated = _bounded(message.text, _PROMPT_MAX_CHARS)
        prompts.append({"text": text, "chars": len(message.text), "truncated": truncated})
    fields: dict[str, Any] = {"prompts": prompts, "prompt_count": submitted.count}
    session_id = next((m.trace_session_id for m in submitted.messages if m.trace_session_id), "")
    if session_id:
        fields["trace_session_id"] = session_id[:_FIELD_MAX_CHARS]
    return fields


def _outcome_fields(run: TaskRun) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "error": _bounded(run.error, _ERROR_MAX_CHARS)[0],
        "work_status": run.work_outcome.status.value,
        "delivery": [
            {
                "provider": outcome.provider.value,
                "chat_id": outcome.chat_id[:_FIELD_MAX_CHARS],
                "ok": outcome.ok,
                "attempts": outcome.attempts,
                "error": _bounded(outcome.error, _FIELD_MAX_CHARS)[0],
            }
            for outcome in run.targets[:_DELIVERY_KEPT]
        ],
    }
    if run.work_outcome.error_kind:
        fields["work_error_kind"] = run.work_outcome.error_kind[:_FIELD_MAX_CHARS]
    # None means no report was retained; an empty string is a known quiet run.
    if run.report is not None:
        report, truncated = _bounded(run.report, _REPORT_MAX_CHARS)
        fields.update(report=report, report_chars=len(run.report), report_truncated=truncated)
        if run.report_summary:
            fields["report_summary"] = _bounded(run.report_summary, _SUMMARY_MAX_CHARS)[0]
    return fields


def build_run_record(
    task: ScheduledTask,
    claim: ExecutionClaim,
    *,
    run: TaskRun | None,
    submitted: SubmittedMessages | None,
) -> dict[str, Any]:
    """Project one attempt to its record: identity, saved task, messages sent, outcome."""
    record: dict[str, Any] = {
        "version": RUN_RECORD_VERSION,
        "task_id": task.id,
        "loop_id": task.params.get(LOOP_GROUP_ID_PARAM, "").strip() or task.id,
        "fire_time": claim.fire_time,
        "attempt": claim.attempt,
        "trigger": _trigger(claim.fire_time),
        "status": (run.status if run is not None else TaskStatus.RUNNING).value,
        "started_at": run.started_at if run is not None else "",
        "finished_at": (run.finished_at or "") if run is not None else "",
        # A delivery-only retry re-sends the report an earlier attempt built.
        "replayed_report": claim.report is not None,
        "task": registry_entry(task),
    }
    if builder := _report_builder(task):
        record["report_builder"] = builder
    if submitted is not None:
        record.update(_submitted_fields(submitted))
    if run is not None and run.status is not TaskStatus.RUNNING:
        record.update(_outcome_fields(run))
    return record


def record_run_started(task: ScheduledTask, claim: ExecutionClaim) -> None:
    """Save the attempt as running, with the task as it was saved when claimed."""
    try:
        save_run_record(build_run_record(task, claim, run=get_claim_run(claim), submitted=None))
    except Exception:
        logger.warning("Failed to record the start of task %s", task.id, exc_info=True)


def record_run_finished(
    task: ScheduledTask, claim: ExecutionClaim, submitted: SubmittedMessages | None
) -> None:
    """Save the attempt's final state from the run row it owns, then report it."""
    try:
        record = build_run_record(task, claim, run=get_claim_run(claim), submitted=submitted)
    except Exception:
        logger.warning("Failed to build the run record of task %s", task.id, exc_info=True)
        return
    if record["status"] == TaskStatus.RUNNING.value:
        # The attempt stopped without completing its row (a lost claim or a
        # crash). It cannot finish now; a retry runs as a new attempt.
        record["status"] = TaskStatus.ABANDONED.value
        record["error"] = record.get("error") or "Stopped before completing."
    try:
        save_run_record(record)
    except Exception:
        logger.warning("Failed to save the run record of task %s", task.id, exc_info=True)
    _report_run_record(record)


def _report_run_record(record: dict[str, Any]) -> None:
    try:
        from infrastructure.analytics.events import Event
        from infrastructure.analytics.provider import analytics_opted_out, get_analytics

        if analytics_opted_out():
            return
        get_analytics().capture(Event.SCHEDULED_TASK_RUN_RECORDED, record)
    except Exception:
        logger.debug(
            "Failed to report a run record for task %s", record.get("task_id"), exc_info=True
        )


__all__ = ["build_run_record", "record_run_finished", "record_run_started"]
