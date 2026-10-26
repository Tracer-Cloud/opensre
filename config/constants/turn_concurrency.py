"""Turn-concurrency environment variable names.

The process-wide turn gate (:mod:`infrastructure.turn_host.concurrency`) and the
heavy-work gate (:mod:`infrastructure.process.turn_capacity.heavy_work`) read
these; holding the names here lets any layer reference them without importing
a gate.
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

# Cap on sessions whose agent the turn host keeps cached between turns. Each
# cached agent holds its session's context in memory; idle sessions beyond the
# cap are evicted least recently used first and rebuilt on their next turn.
OPENSRE_MAX_CACHED_SESSION_AGENTS_ENV = "OPENSRE_MAX_CACHED_SESSION_AGENTS"

DEFAULT_MAX_CACHED_SESSION_AGENTS = 64

# Cap on memory-heavy child work running at once in one process (coding-agent
# CLIs, git clones, CI-repair workers). Separate from the turn cap: a turn mostly
# waits on the LLM, while each of these holds hundreds of megabytes in the same
# container. Unset keeps the default; a non-positive or unparseable value is
# ignored with a warning.
OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV = "OPENSRE_MAX_CONCURRENT_HEAVY_WORK"

DEFAULT_HEAVY_WORK_CONCURRENCY = 2

# How long heavy work waits for a slot before the tool reports that too many
# heavy operations are running.
HEAVY_WORK_WAIT_SECONDS = 300.0

__all__ = [
    "DEFAULT_HEAVY_WORK_CONCURRENCY",
    "DEFAULT_MAX_CACHED_SESSION_AGENTS",
    "DEFAULT_SCHEDULED_RUN_CONCURRENCY",
    "HEAVY_WORK_WAIT_SECONDS",
    "OPENSRE_MAX_CACHED_SESSION_AGENTS_ENV",
    "OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV",
    "OPENSRE_MAX_CONCURRENT_TURNS_ENV",
    "OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV",
    "OPENSRE_SIZE_PROFILE_ENV",
]
