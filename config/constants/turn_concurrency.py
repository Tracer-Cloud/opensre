"""Turn-concurrency environment variable names.

The process-wide turn gate (:mod:`infrastructure.turn_host.concurrency`) reads
these; holding the names here lets any layer reference them without importing
the gate.
"""

from __future__ import annotations

# Deployment size tier (SMALL / MEDIUM / LARGE) mapping to a default concurrent-
# turn limit and matching task resources.
OPENSRE_SIZE_PROFILE_ENV = "OPENSRE_SIZE_PROFILE"

# Explicit cap on concurrent turns, overriding the size-profile default. Lets a
# deployment raise concurrency on the same task size; unset keeps the default.
OPENSRE_MAX_CONCURRENT_TURNS_ENV = "OPENSRE_MAX_CONCURRENT_TURNS"

# Maximum scheduled callbacks that may execute concurrently. Scheduled agent
# turns still take a permit from the process-wide turn gate as a second cap.
OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV = "OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS"

DEFAULT_SCHEDULED_RUN_CONCURRENCY = 2

__all__ = [
    "DEFAULT_SCHEDULED_RUN_CONCURRENCY",
    "OPENSRE_MAX_CONCURRENT_TURNS_ENV",
    "OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV",
    "OPENSRE_SIZE_PROFILE_ENV",
]
