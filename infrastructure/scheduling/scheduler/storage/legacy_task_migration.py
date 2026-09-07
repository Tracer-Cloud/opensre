"""Migrate scheduler rows written before retired task kinds were removed."""

from __future__ import annotations

from collections.abc import Iterable

from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_LEGACY_TASK_KIND_PARAM,
    LOOP_MIGRATION_NOTICE_PARAM,
    LOOP_PROMPT_PARAM,
)
from infrastructure.scheduling.scheduler.types import TaskKind

_CUSTOM_INVESTIGATION = "custom_investigation"
_RETIRED_TASK_KINDS = frozenset(
    {
        _CUSTOM_INVESTIGATION,
        "daily_summary",
        "weekly_audit",
        "incident_window_replay",
        "synthetic_run",
    }
)


def migrate_legacy_task_entries(entries: Iterable[object]) -> bool:
    """Normalize retired task kinds in place before strict model validation."""
    changed = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        legacy_kind = entry.get("kind")
        if legacy_kind not in _RETIRED_TASK_KINDS:
            continue

        params = entry.get("params")
        if not isinstance(params, dict):
            params = {}
            entry["params"] = params

        prompt = params.get(LOOP_PROMPT_PARAM)
        entry["kind"] = TaskKind.MANUAL_LOOP.value
        changed = True
        if legacy_kind == _CUSTOM_INVESTIGATION and isinstance(prompt, str) and prompt.strip():
            continue

        task_id = str(entry.get("id") or "<task-id>")
        entry["enabled"] = False
        params[LOOP_LEGACY_TASK_KIND_PARAM] = legacy_kind
        params[LOOP_MIGRATION_NOTICE_PARAM] = (
            f"Legacy task kind '{legacy_kind}' depended on the retired investigation scheduler "
            "and was disabled. Recreate it with 'opensre cron add --kind manual_loop "
            f"--prompt <instruction> ...', then remove this task with 'opensre cron remove {task_id}'."
        )
    return changed


__all__ = ["migrate_legacy_task_entries"]
