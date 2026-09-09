"""Per-kind message builders for scheduled tasks.

Each task kind maps to a function that produces a formatted report string
suitable for delivery to messaging providers. Builders dispatch through the
agent runner registered on :class:`SchedulerRunners`; failures raise
RuntimeError so the executor records FAILED in cron logs without masking
outages as success.
"""

from __future__ import annotations

import logging

from core.agent_harness import (
    is_legacy_skill_name,
    normalize_skill_name,
    pin_recurring_skill,
    resolve_scheduled_skill,
)
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.operation_log import record_scheduler_task_operation
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import update_task
from infrastructure.scheduling.scheduler.types import ScheduledTask, TaskKind

logger = logging.getLogger(__name__)

# Keys that should never be forwarded to the agent runner
_CREDENTIAL_KEYS = frozenset({"bot_token", "access_token", "api_key", "webhook_url", "secret"})


def build_message(task: ScheduledTask, runners: SchedulerRunners) -> str:
    """Build the report message for a scheduled task based on its kind.

    Returns the formatted message string. Raises RuntimeError on unrecoverable
    runner failures.
    """
    builders = {
        TaskKind.MANUAL_LOOP: _build_manual_loop,
        TaskKind.SENTRY_MORNING_DIGEST: _build_sentry_morning_digest,
        TaskKind.SENTRY_UPTIME_WATCH: _build_sentry_uptime_watch,
        TaskKind.GITHUB_PR_SWEEP: _build_github_pr_sweep,
        TaskKind.POSTHOG_METRIC_REPORT: _build_posthog_metric_report,
        TaskKind.WORK_ITEM_REMINDER: _build_work_item_reminder,
        TaskKind.WORK_ITEM_CHECKIN: _build_work_item_checkin,
        TaskKind.RECURRING_SKILL: _build_recurring_skill,
    }
    builder = builders.get(task.kind)
    if builder is None:
        return f"⚠️ Unknown task kind: {task.kind}"
    return builder(task, runners)


def _build_sentry_morning_digest(task: ScheduledTask, runners: SchedulerRunners) -> str:
    """Build a Sentry morning digest via the headless summarizing-sentry-issues skill path."""
    try:
        safe_params = {k: v for k, v in task.params.items() if k not in _CREDENTIAL_KEYS}
        payload = {
            "stats_period": "24h",
            "query": "is:unresolved",
            **safe_params,
            "source": "scheduled_sentry_morning_digest",
            "task_id": task.id,
        }
        return runners.agent(payload)
    except Exception as exc:
        logger.error("Sentry morning digest failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"Sentry morning digest failed for task {task.id}. Check logs for details."
        ) from exc


def _build_sentry_uptime_watch(task: ScheduledTask, runners: SchedulerRunners) -> str:
    """Poll Sentry uptime monitors; return a notify body only on transitions.

    Quiet ticks return an empty string so the executor skips delivery (#4032 v1).
    Implemented via the agent-runner port so ``platform`` does not import
    ``integrations`` (layering).
    """
    try:
        safe_params = {k: v for k, v in task.params.items() if k not in _CREDENTIAL_KEYS}
        payload = {
            **safe_params,
            "source": "scheduled_sentry_uptime_watch",
            "task_id": task.id,
        }
        return runners.agent(payload)
    except Exception as exc:
        logger.error("Sentry uptime watch failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"Sentry uptime watch failed for task {task.id}. Check logs for details."
        ) from exc


def _build_github_pr_sweep(task: ScheduledTask, runners: SchedulerRunners) -> str:
    """Build a GitHub PR sweep digest via the headless agent path."""
    try:
        safe_params = {k: v for k, v in task.params.items() if k not in _CREDENTIAL_KEYS}
        payload = {
            **safe_params,
            "source": "scheduled_github_pr_sweep",
            "task_id": task.id,
        }
        return runners.agent(payload)
    except Exception as exc:
        logger.error("GitHub PR sweep failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"GitHub PR sweep failed for task {task.id}. Check logs for details."
        ) from exc


def _build_posthog_metric_report(task: ScheduledTask, runners: SchedulerRunners) -> str:
    """Build a PostHog per-metric report via the headless summarizing-posthog-analytics skill path."""
    try:
        safe_params = {k: v for k, v in task.params.items() if k not in _CREDENTIAL_KEYS}
        payload = {
            "stats_period": "7d",
            **safe_params,
            "source": "scheduled_posthog_metric_report",
            "task_id": task.id,
        }
        return runners.agent(payload)
    except Exception as exc:
        logger.error("PostHog metric report failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"PostHog metric report failed for task {task.id}. Check logs for details."
        ) from exc


def _build_work_item_reminder(task: ScheduledTask, _runners: SchedulerRunners) -> str:
    """Build a one-shot reminder for a durable work item.

    Missing or already-completed work items produce a quiet tick so stale
    reminders do not spam the channel. This builder does not stamp
    ``last_reminded_at``; the executor does that after a successful send.
    """
    from pathlib import Path

    from core.domain.work_items import (
        WorkItemStatus,
        build_work_item_reminder_message,
        get_work_item,
    )

    item_id = task.params.get("work_item_id", "").strip()
    if not item_id:
        return ""
    store_path_text = task.params.get("store_path", "").strip()
    store_path = Path(store_path_text).expanduser() if store_path_text else None
    item = get_work_item(item_id, store_path=store_path)
    if item is None or item.status is WorkItemStatus.COMPLETED:
        return ""
    return build_work_item_reminder_message(item)


def _build_work_item_checkin(task: ScheduledTask, _runners: SchedulerRunners) -> str:
    """Build a recurring prioritization check-in for durable work items."""
    from pathlib import Path

    from core.domain.work_items import build_work_item_checkin_message, list_work_items

    store_path_text = task.params.get("store_path", "").strip()
    store_path = Path(store_path_text).expanduser() if store_path_text else None
    project = task.params.get("project", "").strip()
    try:
        limit = max(int(task.params.get("limit", "5")), 1)
    except ValueError:
        limit = 5
    items = list_work_items(status=None, project=project, store_path=store_path)
    active_items = [item for item in items if item.is_active]
    return build_work_item_checkin_message(active_items, project=project, limit=limit)


def _build_manual_loop(task: ScheduledTask, runners: SchedulerRunners) -> str:
    """Build a manual prompt-loop report via the headless assistant path.

    On failure, raises RuntimeError so the executor records the failure
    without leaking exception details to the chat.
    """
    try:
        safe_params = {k: v for k, v in task.params.items() if k not in _CREDENTIAL_KEYS}
        prompt = safe_params.get(LOOP_PROMPT_PARAM, "").strip()
        if not prompt:
            return f"⚠️ Manual loop task {task.id} has no prompt configured."
        payload = {
            **safe_params,
            "source": "scheduled_manual_loop",
            "task_id": task.id,
            "name": task.name,
            "task_name": task.name,
            "loop_prompt": prompt,
            "window_hours": task.window_hours,
        }
        return runners.agent(payload)
    except Exception as exc:
        logger.error("Manual loop failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"Manual loop failed for task {task.id}. Check logs for details."
        ) from exc


def _build_recurring_skill(task: ScheduledTask, runners: SchedulerRunners) -> str:
    """Run a pinned recurring action skill via the headless agent path."""
    skill_name = task.skill_name.strip()
    if not skill_name:
        raise RuntimeError(f"Recurring skill task {task.id} is missing skill_name.")
    if is_legacy_skill_name(skill_name):
        _migrate_renamed_skill(task)
    resolve_scheduled_skill(task.skill_name, task.skill_revision)
    return runners.agent(
        {
            "source": "scheduled_recurring_skill",
            "task_id": task.id,
            "skill_name": task.skill_name,
            "skill_revision": task.skill_revision,
            "skill_inputs": dict(task.skill_inputs),
        }
    )


def _migrate_renamed_skill(task: ScheduledTask) -> None:
    """Move a persisted schedule from a retired skill slug to its successor.

    The revision pin is recomputed because a rename rewrites the body's own
    name references; only slugs listed in ``LEGACY_SKILL_NAMES`` qualify, so
    this never accepts an arbitrary recipe change unattended.
    """
    previous = task.skill_name
    skill_name, skill_revision = pin_recurring_skill(normalize_skill_name(previous))
    task.skill_name = skill_name
    task.skill_revision = skill_revision
    if not update_task(task):
        logger.warning(
            "Recurring skill task %s (%s -> %s) is not in the task store; running unmigrated.",
            task.id,
            previous,
            skill_name,
        )
        return
    record_scheduler_task_operation(
        "scheduled_skill_renamed",
        task,
        extra={"from_skill_name": previous},
    )


__all__ = ["build_message"]
