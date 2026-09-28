"""Activation milestones of one repair run, recorded on whichever host reaches them.

The ``remote_*`` milestones belong to runs a gateway's own scheduler owns; the same
loop scheduled from the user's shell records only the test failure it triggered.
"""

from __future__ import annotations

import time
from typing import Any

from infrastructure.analytics.capture import (
    capture_remote_ci_failure_detected,
    capture_remote_ci_monitoring_started,
    capture_remote_ci_repair_succeeded,
    capture_test_ci_failure_triggered,
)
from integrations.github.tools.ci_repair_loop.models import RepairRun


def _identity(run: RepairRun) -> dict[str, Any]:
    return {
        "repair_run_id": run.id,
        "repository": f"{run.owner}/{run.repo}",
        "pr_number": run.pr_number,
        "demo": run.demo,
    }


def monitoring_started(run: RepairRun) -> None:
    if run.remote:
        capture_remote_ci_monitoring_started(**_identity(run))


def demo_failure_triggered(run: RepairRun) -> None:
    capture_test_ci_failure_triggered(**_identity(run), remote=run.remote)


def failure_detected(run: RepairRun) -> None:
    if run.remote:
        capture_remote_ci_failure_detected(**_identity(run))


def repair_succeeded(run: RepairRun) -> None:
    if run.remote:
        capture_remote_ci_repair_succeeded(
            **_identity(run),
            attempts=run.attempts,
            duration_ms=max(0.0, time.time() - run.started_at) * 1000,
        )
