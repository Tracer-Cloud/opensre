"""Record what each scheduled run attempt ran and how it ended.

A record holds the task as saved when the attempt was claimed, the exact
messages its turns submitted, the report it built, every delivery outcome, the
tool calls that changed something and the note its reply left for the next
run, so a later edit to the loop never changes an earlier record. It is written
to the run-record file when an attempt starts and replaced when it ends, from
the run row the attempt owns, so a stale worker only ever writes its own
attempt. When an attempt ends, the record is also sent as
``scheduled_task_run_recorded``, because dashboards read analytics events and
never gateway files.

Version 2 added ``actions``, ``action_count`` and ``carry_note``; a version 1
record has no actions or note because none were tracked. Text is
credential-redacted and capped. Recording never fails a run.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from infrastructure.observability.trace.submitted_messages import SubmittedMessages
from infrastructure.safety.secret_redaction import redact_text
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_GROUP_ID_PARAM,
    LOOP_REPORT_ARGS_PARAM,
    LOOP_REPORT_PARAM,
)
from infrastructure.scheduling.scheduler.registry_telemetry import registry_entry
from infrastructure.scheduling.scheduler.run_activity import RunActivity
from infrastructure.scheduling.scheduler.storage.run_record_store import save_run_record
from infrastructure.scheduling.scheduler.storage.run_store import ExecutionClaim, get_claim_run
from infrastructure.scheduling.scheduler.types import ScheduledTask, TaskRun, TaskStatus

logger = logging.getLogger(__name__)

RUN_RECORD_VERSION = 2
#: Text caps are UTF-8 bytes: the analytics sender limits a payload to 256 KiB.
_PROMPT_MAX_BYTES = 16_000
_PROMPTS_KEPT = 3
_REPORT_MAX_BYTES = 32_000
_SUMMARY_MAX_BYTES = 1_000
_ERROR_MAX_BYTES = 600
_BUILDER_ARGS_MAX_BYTES = 1_000
_FIELD_MAX_BYTES = 400
_DELIVERY_KEPT = 40
#: An action is at most 200 characters and a note 300; these bound their bytes.
_ACTION_MAX_BYTES = 800
_CARRY_NOTE_MAX_BYTES = 1_200
#: The serialized record must fit one event with room for base properties.
_EVENT_MAX_BYTES = 192 * 1024


def _cut(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Leave room for the ellipsis; a split multi-byte character is dropped.
    return encoded[: max_bytes - 3].decode("utf-8", errors="ignore").rstrip() + "…"


def _bounded(value: str, max_bytes: int) -> tuple[str, bool]:
    """Redact credentials, then cap at ``max_bytes`` UTF-8 bytes."""
    text = redact_text(value)
    cut = _cut(text, max_bytes)
    return cut, cut != text


def _trigger(fire_time: str) -> str:
    """Scheduled fires use second precision; a manual run's key carries microseconds."""
    return "manual" if "." in fire_time else "schedule"


def _report_builder(task: ScheduledTask) -> dict[str, str] | None:
    """The deterministic builder a loop names; it submits no model message."""
    name = task.params.get(LOOP_REPORT_PARAM, "").strip()
    if not name:
        return None
    args = _bounded(task.params.get(LOOP_REPORT_ARGS_PARAM, "").strip(), _BUILDER_ARGS_MAX_BYTES)[0]
    return {"name": _bounded(name, _FIELD_MAX_BYTES)[0], "args": args}


def _submitted_fields(submitted: SubmittedMessages) -> dict[str, Any]:
    prompts = []
    for message in submitted.messages[:_PROMPTS_KEPT]:
        text, truncated = _bounded(message.text, _PROMPT_MAX_BYTES)
        prompts.append({"text": text, "chars": len(message.text), "truncated": truncated})
    fields: dict[str, Any] = {"prompts": prompts, "prompt_count": submitted.count}
    session_id = next((m.trace_session_id for m in submitted.messages if m.trace_session_id), "")
    if session_id:
        fields["trace_session_id"] = _bounded(session_id, _FIELD_MAX_BYTES)[0]
    return fields


def _delivery_fields(run: TaskRun) -> dict[str, Any]:
    """Per-destination outcomes in plan order; past the cap, failures are kept first."""
    targets = list(run.targets)
    if len(targets) > _DELIVERY_KEPT:
        kept = {id(outcome) for outcome in sorted(targets, key=lambda o: o.ok)[:_DELIVERY_KEPT]}
        targets = [outcome for outcome in targets if id(outcome) in kept]
    return {
        "delivery_count": len(run.targets),
        "delivery": [
            {
                "provider": outcome.provider.value,
                "chat_id": _bounded(outcome.chat_id, _FIELD_MAX_BYTES)[0],
                "ok": outcome.ok,
                "attempts": outcome.attempts,
                "error": _bounded(outcome.error, _FIELD_MAX_BYTES)[0],
            }
            for outcome in targets
        ],
    }


def _outcome_fields(run: TaskRun) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "error": _bounded(run.error, _ERROR_MAX_BYTES)[0],
        "work_status": run.work_outcome.status.value,
        **_delivery_fields(run),
    }
    if run.work_outcome.error_kind:
        fields["work_error_kind"] = _bounded(run.work_outcome.error_kind, _FIELD_MAX_BYTES)[0]
    # None means no report was retained; an empty string is a known quiet run.
    if run.report is not None:
        report, truncated = _bounded(run.report, _REPORT_MAX_BYTES)
        fields.update(report=report, report_chars=len(run.report), report_truncated=truncated)
        if run.report_summary:
            fields["report_summary"] = _bounded(run.report_summary, _SUMMARY_MAX_BYTES)[0]
    return fields


def _activity_fields(activity: RunActivity) -> dict[str, Any]:
    """The calls that changed something, newest kept, and the note for the next run."""
    snapshot = activity.snapshot()
    fields: dict[str, Any] = {
        "actions": [_bounded(action, _ACTION_MAX_BYTES)[0] for action in snapshot.actions],
        "action_count": snapshot.action_count,
    }
    if snapshot.carry_note:
        fields["carry_note"] = _bounded(snapshot.carry_note, _CARRY_NOTE_MAX_BYTES)[0]
    return fields


def _serialized_bytes(record: dict[str, Any]) -> int:
    # The analytics sender serializes the same way.
    return len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _fit_one_event(record: dict[str, Any]) -> None:
    """Halve the report and prompts until the record fits one analytics event.

    Byte caps alone can be exceeded by JSON escaping (quotes, control characters).
    """
    for _ in range(10):
        if _serialized_bytes(record) <= _EVENT_MAX_BYTES:
            return
        if report := record.get("report"):
            record["report"] = _cut(report, len(report.encode("utf-8")) // 2)
            record["report_truncated"] = True
        for prompt in record.get("prompts", []):
            prompt["text"] = _cut(prompt["text"], len(prompt["text"].encode("utf-8")) // 2)
            prompt["truncated"] = True


def build_run_record(
    task: ScheduledTask,
    claim: ExecutionClaim,
    *,
    run: TaskRun | None,
    submitted: SubmittedMessages | None,
    activity: RunActivity | None = None,
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
    # A task's own organization attributes the event, as for loop reports.
    if task.organization.strip():
        record["organization_id"] = task.organization.strip()
    if builder := _report_builder(task):
        record["report_builder"] = builder
    if submitted is not None:
        record.update(_submitted_fields(submitted))
    if run is not None and run.status is not TaskStatus.RUNNING:
        record.update(_outcome_fields(run))
    if activity is not None:
        record.update(_activity_fields(activity))
    _fit_one_event(record)
    return record


def record_run_started(task: ScheduledTask, claim: ExecutionClaim) -> None:
    """Save the attempt as running, with the task as it was saved when claimed."""
    try:
        save_run_record(build_run_record(task, claim, run=get_claim_run(claim), submitted=None))
    except Exception:
        logger.warning("Failed to record the start of task %s", task.id, exc_info=True)


def record_run_finished(
    task: ScheduledTask,
    claim: ExecutionClaim,
    submitted: SubmittedMessages | None,
    activity: RunActivity | None = None,
) -> None:
    """Save the attempt's final state from the run row it owns, then report it."""
    try:
        record = build_run_record(
            task, claim, run=get_claim_run(claim), submitted=submitted, activity=activity
        )
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
        # One event per attempt: a resend replaces the stored row instead of adding one.
        key = f"opensre:{Event.SCHEDULED_TASK_RUN_RECORDED}:{record['task_id']}:{record['fire_time']}:{record['attempt']}"
        get_analytics().capture(
            Event.SCHEDULED_TASK_RUN_RECORDED,
            record,
            event_id=str(uuid5(NAMESPACE_URL, key)),
            occurred_at=record.get("finished_at") or None,
        )
    except Exception:
        logger.debug(
            "Failed to report a run record for task %s", record.get("task_id"), exc_info=True
        )


__all__ = ["build_run_record", "record_run_finished", "record_run_started"]
