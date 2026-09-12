"""Pin one repair scope and deadline before registering its real cron trigger."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import uuid

from filelock import FileLock, Timeout

from config.constants.ci_repair import CI_REPAIR_CRON, CI_REPAIR_REPORT_BUILDER, CI_REPAIR_SECONDS
from config.constants.github import GITHUB_CI_DEMO_REPOSITORY
from infrastructure.scheduling.scheduler.background_service import ensure_background_service
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_MODE_AGENT,
    LOOP_MODE_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_REPORT_ARGS_PARAM,
    LOOP_REPORT_PARAM,
)
from infrastructure.scheduling.scheduler.runner import compute_next_run
from infrastructure.scheduling.scheduler.storage import add_task, get_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.github.client import GitHubRestClient
from integrations.github.tools.ci_repair_loop.credentials import account_id, configured_token
from integrations.github.tools.ci_repair_loop.fixture import object_response
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.storage import RepairStore
from integrations.github.tools.ci_repair_loop.supervisor import finish_run

logger = logging.getLogger(__name__)


def _component(value: str) -> str:
    if not value or re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None or value in {".", ".."}:
        raise ValueError("Use an explicit GitHub owner and repository name.")
    return value


def schedule_repair(
    *,
    demo: bool,
    owner: str = "",
    repo: str = "",
    pr_number: int = 0,
    github_token: str | None = None,
    store: RepairStore | None = None,
) -> tuple[RepairRun, bool, str | None]:
    """Schedule once per active target; repeated requests retain the original deadline."""
    started = time.time()
    token = configured_token(github_token)
    user = object_response(GitHubRestClient(token).request("GET", "user"))
    actor_id = account_id(user)
    actor = _component(str(user.get("login") or ""))
    owner = _component(owner.strip() or actor)
    if demo:
        if pr_number or repo and repo != GITHUB_CI_DEMO_REPOSITORY:
            raise ValueError(
                "Demo mode uses only the fixed demo repository and creates its own PR."
            )
        repo = GITHUB_CI_DEMO_REPOSITORY
    elif pr_number <= 0:
        raise ValueError("Select a PR number or request demo=true.")
    repo = _component(repo.strip())
    store = store or RepairStore()
    with FileLock(str(store.root / "schedule.lock"), timeout=30):
        run, reused = store.reserve(
            RepairRun(
                id=uuid.uuid4().hex[:12],
                owner=owner,
                repo=repo,
                actor=actor,
                actor_id=actor_id,
                demo=demo,
                started_at=started,
                deadline=started + CI_REPAIR_SECONDS,
                pr_number=pr_number,
            )
        )
        existing = get_task(run.id)
        if reused and existing is not None and existing.enabled:
            return run, True, existing.next_run
        if reused and (
            existing is not None or run.registered or run.status is not RepairStatus.QUEUED
        ):
            try:
                with FileLock(str(store.directory(run.id)) + ".execution.lock", timeout=0):
                    run = store.get(run.id)
                    run.status, run.reason = (
                        RepairStatus.CANCELLED,
                        "The previous repair schedule was stopped.",
                    )
                    finish_run(store, run)
            except Timeout:
                # The supervisor owns cancellation until its worker has stopped.
                run = store.get(run.id)
            return run, True, None
        try:
            ensure_background_service(deadline=run.deadline)
            if time.time() >= run.deadline:
                run.status, run.reason = (
                    RepairStatus.TIMED_OUT,
                    "Setup exhausted the ten-minute budget.",
                )
                finish_run(store, run)
                return run, reused, None
            if existing is None:
                task = ScheduledTask(
                    id=run.id,
                    name=f"CI repair: {owner}/{repo}",
                    kind=TaskKind.MANUAL_LOOP,
                    cron=CI_REPAIR_CRON,
                    timezone="UTC",
                    provider=Provider.INTERACTIVE_SHELL,
                    params={
                        LOOP_PROMPT_PARAM: f"Repair only {owner}/{repo}; retain the terminal evidence report.",
                        LOOP_MODE_PARAM: LOOP_MODE_AGENT,
                        LOOP_REPORT_PARAM: CI_REPAIR_REPORT_BUILDER,
                        LOOP_REPORT_ARGS_PARAM: json.dumps({"run_id": run.id}),
                    },
                )
                task.next_run = compute_next_run(task)
                existing = add_task(task)
                run = store.mark_registered(run.id)
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            logger.exception("CI repair registration failed")
            run.status, run.reason = (
                RepairStatus.FAILED,
                "Could not register the background repair; check the local scheduler log.",
            )
            finish_run(store, run)
            return run, reused, None
    return run, reused, existing.next_run
