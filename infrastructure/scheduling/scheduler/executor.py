"""Task execution with claim-based dedup and per-provider delivery."""

from __future__ import annotations

import logging
from collections.abc import Callable

from infrastructure.scheduling.scheduler.claim_lease import (
    ClaimOwnership,
    default_claim_lease_renewer,
)
from infrastructure.scheduling.scheduler.delivery_bundle import resolve_delivery_adapter
from infrastructure.scheduling.scheduler.delivery_plan import (
    DeliveryTarget,
    TargetKey,
    resolve_delivery_plan,
)
from infrastructure.scheduling.scheduler.fanout import FanOutResult, deliver_plan
from infrastructure.scheduling.scheduler.loop_constants import LOOP_CHANNELS_PARAM
from infrastructure.scheduling.scheduler.operation_log import record_scheduler_execution_operation
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import (
    ExecutionClaim,
    complete_run,
    try_claim,
)
from infrastructure.scheduling.scheduler.tasks import build_message
from infrastructure.scheduling.scheduler.types import (
    DeliveryStatus,
    ScheduledTask,
    TaskKind,
    TaskStatus,
)

logger = logging.getLogger(__name__)


def execute_task(
    task: ScheduledTask,
    fire_time: str,
    runners: SchedulerRunners,
    *,
    target_filter: frozenset[TargetKey] | None = None,
) -> bool:
    """Execute a scheduled task with claim-based dedup.

    Args:
        task: The scheduled task definition.
        fire_time: The canonical fire time string (UTC, minute-precision) from the
            scheduler trigger, used as the dedup key.
        target_filter: When given, narrows delivery to destinations whose
            ``(provider, chat_id)`` is in the set -- a rerun retrying only the
            destinations a previous run failed at.

    Returns:
        True if the task was executed and delivered successfully.
        False if the claim was lost (another instance handled it) or delivery failed.
    """
    # Attempt to claim this execution slot
    claim = try_claim(task.id, fire_time, target_filter=target_filter)
    if claim is None:
        logger.info(
            "Task %s fire_time=%s already claimed by another instance",
            task.id,
            fire_time,
        )
        record_scheduler_execution_operation(
            "scheduled_task_execution_skipped",
            task,
            fire_time=fire_time,
            status=TaskStatus.SKIPPED,
            extra={"reason": "already_claimed"},
        )
        return False

    with default_claim_lease_renewer.hold(claim) as ownership:
        return _execute_claimed_task(claim, ownership, task, fire_time, runners)


def _execute_claimed_task(
    claim: ExecutionClaim,
    ownership: ClaimOwnership,
    task: ScheduledTask,
    fire_time: str,
    runners: SchedulerRunners,
) -> bool:
    """Build and deliver a task while its fenced lease remains valid."""
    logger.info("Executing task %s (kind=%s, fire_time=%s)", task.id, task.kind, fire_time)
    record_scheduler_execution_operation(
        "scheduled_task_execution_started",
        task,
        fire_time=fire_time,
        status=TaskStatus.RUNNING,
    )
    _emit_analytics_started(task)

    if claim.target_filter == frozenset():
        _record_failure(
            claim,
            task,
            fire_time,
            "No delivery destinations authorized for this attempt; run the task explicitly.",
            stage="delivery_scope",
        )
        return False

    # Build the message
    try:
        message = build_message(task, runners)
    except RuntimeError as exc:
        # Pipeline failures — record without leaking details to chat
        _record_failure(claim, task, fire_time, str(exc), stage="message_build")
        return False
    except Exception as exc:
        _record_failure(
            claim,
            task,
            fire_time,
            f"Message build error: {type(exc).__name__}",
            stage="message_build",
        )
        return False

    if not ownership.valid():
        logger.warning(
            "Skipping delivery after losing scheduler claim for task %s fire_time=%s",
            task.id,
            fire_time,
        )
        return False

    # Quiet ticks (e.g. uptime watch with no transitions) skip delivery.
    if not message.strip():
        if not complete_run(
            claim,
            status=TaskStatus.SUCCESS,
            posted_message_id="",
            provider=_run_provider_label(task),
        ):
            return False
        _emit_analytics(task, TaskStatus.SUCCESS)
        logger.info("Task %s produced no message; delivery skipped", task.id)
        record_scheduler_execution_operation(
            "scheduled_task_execution_completed",
            task,
            fire_time=fire_time,
            status=TaskStatus.SUCCESS,
            message_chars=0,
            extra={"delivery_skipped": True},
        )
        return True

    # Fan out to every destination the task resolves to, concurrently.
    result = _deliver_all(
        task,
        message,
        target_filter=claim.target_filter,
        can_deliver=ownership.valid,
    )
    if not ownership.valid():
        logger.warning(
            "Discarding delivery result after losing scheduler claim for task %s fire_time=%s",
            task.id,
            fire_time,
        )
        return False
    message_id = result.message_id()
    error = result.error()

    if result.status is DeliveryStatus.FAILED:
        _record_failure(
            claim,
            task,
            fire_time,
            error,
            stage="delivery",
            message_chars=len(message),
            result=result,
        )
        return False

    if not complete_run(
        claim,
        status=TaskStatus.SUCCESS,
        posted_message_id=message_id,
        error=error,
        provider=_run_provider_label(task),
        targets=result.outcomes,
    ):
        return False
    _emit_analytics(task, TaskStatus.SUCCESS, error=error)
    _record_work_item_reminder_delivery(task)
    record_scheduler_execution_operation(
        "scheduled_task_execution_completed",
        task,
        fire_time=fire_time,
        status=TaskStatus.SUCCESS,
        message_chars=len(message),
        message_id=message_id,
        error=error,
        extra={
            "delivery_skipped": False,
            "partial_failure": result.status is DeliveryStatus.PARTIAL,
            "delivery_status": result.status.value,
            "delivery_target_outcomes": _target_outcome_summary(result),
        },
    )
    if result.status is DeliveryStatus.PARTIAL:
        logger.warning(
            "Task %s delivered with partial channel failures (message_id=%s): %s",
            task.id,
            message_id,
            error,
        )
    else:
        logger.info("Task %s delivered successfully (message_id=%s)", task.id, message_id)
    return True


def _record_work_item_reminder_delivery(task: ScheduledTask) -> None:
    """Persist ``last_reminded_at`` only after a reminder was delivered."""
    if task.kind is not TaskKind.WORK_ITEM_REMINDER:
        return
    item_id = task.params.get("work_item_id", "").strip()
    if not item_id:
        return
    from pathlib import Path

    from core.domain.work_items import now_iso, set_work_item_last_reminded

    store_path_text = task.params.get("store_path", "").strip()
    store_path = Path(store_path_text).expanduser() if store_path_text else None
    try:
        set_work_item_last_reminded(item_id, now_iso(), store_path=store_path)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to record last_reminded_at for work item %s after delivery",
            item_id,
            exc_info=True,
        )


def _deliver_single(
    target: DeliveryTarget,
    message: str,
    can_deliver: Callable[[], bool] | None = None,
) -> tuple[bool, str, str]:
    """Deliver one message to one destination via its installed adapter."""
    if can_deliver is not None and not can_deliver():
        return False, "scheduler claim ownership lost", ""
    adapter = resolve_delivery_adapter(target.provider)
    if adapter is None:
        return False, f"Unsupported provider: {target.provider}", ""
    return adapter.deliver(target.task, message)


def _deliver_all(
    task: ScheduledTask,
    message: str,
    *,
    target_filter: frozenset[TargetKey] | None = None,
    can_deliver: Callable[[], bool] | None = None,
) -> FanOutResult:
    """Resolve ``task``'s destinations once and deliver to all of them at once."""
    plan = resolve_delivery_plan(task, only=target_filter)
    return deliver_plan(
        plan,
        message,
        lambda target, content: _deliver_single(target, content, can_deliver),
    )


def _target_outcome_summary(result: FanOutResult) -> tuple[str, ...]:
    """Per-destination outcomes for the operations log, without chat ids."""
    return tuple(
        f"{outcome.provider.value}:{'success' if outcome.ok else 'failed'}:{outcome.attempts}"
        for outcome in result.outcomes
    )


def _run_provider_label(task: ScheduledTask) -> str:
    """Return the provider label persisted with run history."""
    channels = task.params.get(LOOP_CHANNELS_PARAM, "").strip()
    return channels or task.provider.value


def _record_failure(
    claim: ExecutionClaim,
    task: ScheduledTask,
    fire_time: str,
    error: str,
    *,
    stage: str,
    message_chars: int | None = None,
    result: FanOutResult | None = None,
) -> None:
    """Record a failed execution in the claim store and emit analytics."""
    outcomes = result.outcomes if result is not None else ()
    if not complete_run(
        claim,
        status=TaskStatus.FAILED,
        error=error,
        provider=_run_provider_label(task),
        targets=outcomes,
    ):
        return
    _emit_analytics(task, TaskStatus.FAILED, error=error)
    extra: dict[str, object] = {"stage": stage}
    if result is not None:
        extra["delivery_status"] = result.status.value
        extra["delivery_target_outcomes"] = _target_outcome_summary(result)
    record_scheduler_execution_operation(
        "scheduled_task_execution_failed",
        task,
        fire_time=fire_time,
        status=TaskStatus.FAILED,
        message_chars=message_chars,
        error=error,
        extra=extra,
    )
    logger.warning("Task %s failed: %s", task.id, error)


def _emit_analytics_started(task: ScheduledTask) -> None:
    """Emit SCHEDULED_TASK_STARTED event after a claim is won."""
    try:
        from infrastructure.analytics.events import Event
        from infrastructure.analytics.provider import Properties, get_analytics

        properties: Properties = {
            "task_id": task.id,
            "task_kind": task.kind.value,
            "provider": task.provider.value,
        }
        get_analytics().capture(Event.SCHEDULED_TASK_STARTED, properties)
    except Exception:
        logger.debug("Failed to emit analytics for task %s", task.id, exc_info=True)


def _emit_analytics(task: ScheduledTask, status: TaskStatus, error: str = "") -> None:
    """Emit analytics event for task execution completion."""
    try:
        from infrastructure.analytics.events import Event
        from infrastructure.analytics.provider import Properties, get_analytics

        event_name = (
            Event.SCHEDULED_TASK_COMPLETED
            if status == TaskStatus.SUCCESS
            else Event.SCHEDULED_TASK_FAILED
        )
        properties: Properties = {
            "task_id": task.id,
            "task_kind": task.kind.value,
            "provider": task.provider.value,
            "status": status.value,
        }
        if error:
            properties["error"] = error[:200]
        get_analytics().capture(event_name, properties)
    except Exception:
        # Analytics must never crash the scheduler
        logger.debug("Failed to emit analytics for task %s", task.id, exc_info=True)


def deliver_scheduled_message(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver an ad-hoc message using the task's configured provider/chat.

    Used for one-shot notices (e.g. uptime watch activation) outside a cron tick.
    Returns ``(ok, error, message_id)``.
    """
    result = _deliver_all(task, message)
    return result.status is not DeliveryStatus.FAILED, result.error(), result.message_id()


__all__ = ["deliver_scheduled_message", "execute_task"]
