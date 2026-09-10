"""Scheduler task definitions and execution history persistence."""

from infrastructure.scheduling.scheduler.storage.database import (
    default_run_database_path,
    run_database_path,
)
from infrastructure.scheduling.scheduler.storage.run_store import (
    ExecutionClaim,
    RecoverableRun,
    claim_renewal_interval_seconds,
    complete_run,
    delete_runs,
    get_latest_finished_run,
    get_latest_run_for_fire_time,
    get_latest_targeted_run,
    get_recoverable_runs,
    get_runs,
    renew_claims,
    try_claim,
    try_queue_run,
)
from infrastructure.scheduling.scheduler.storage.task_store import (
    add_task,
    default_task_store_path,
    get_task,
    list_tasks,
    record_task_success,
    remove_task,
    update_task,
)

__all__ = [
    "add_task",
    "claim_renewal_interval_seconds",
    "complete_run",
    "default_run_database_path",
    "default_task_store_path",
    "delete_runs",
    "ExecutionClaim",
    "RecoverableRun",
    "get_recoverable_runs",
    "get_latest_finished_run",
    "get_latest_run_for_fire_time",
    "get_latest_targeted_run",
    "get_runs",
    "get_task",
    "list_tasks",
    "record_task_success",
    "remove_task",
    "renew_claims",
    "run_database_path",
    "try_claim",
    "try_queue_run",
    "update_task",
]
