"""Limits and storage names for bounded GitHub CI repair loops."""

CI_REPAIR_WORKER_COMMAND = "_ci-repair-worker"
CI_REPAIR_SECONDS = 600
CI_REPAIR_FINISH_RESERVE_SECONDS = 15
#: A failing repair stops here so a background loop cannot keep spending
#: coding-agent credits until the ten-minute deadline.
CI_REPAIR_MAX_ATTEMPTS = 3
CI_REPAIR_POLL_SECONDS = 1.0
CI_REPAIR_DIRECTORY = "ci-repair"
CI_REPAIR_REPORT_BUILDER = "github_ci_repair"
#: Six-field cron (leading seconds field): poll every 30 seconds. A slower
#: cadence leaves failing PRs unrepaired for too long; never widen it.
CI_REPAIR_CRON = "*/30 * * * * *"

__all__ = [
    "CI_REPAIR_CRON",
    "CI_REPAIR_DIRECTORY",
    "CI_REPAIR_FINISH_RESERVE_SECONDS",
    "CI_REPAIR_MAX_ATTEMPTS",
    "CI_REPAIR_POLL_SECONDS",
    "CI_REPAIR_REPORT_BUILDER",
    "CI_REPAIR_SECONDS",
    "CI_REPAIR_WORKER_COMMAND",
]
