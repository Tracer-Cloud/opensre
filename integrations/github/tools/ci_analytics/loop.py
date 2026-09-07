"""The recurring CI reliability check: a prompt loop that re-runs the analytics for one repository."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from config.runtime_metadata.probes import local_tz_name
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.loops import (
    ManualLoop,
    create_manual_loop,
    loop_channels,
    loop_time_label,
)
from infrastructure.scheduling.scheduler.storage import list_tasks
from infrastructure.scheduling.scheduler.types import Provider, TaskKind

DEFAULT_LOOP_TIME = "08:00"
LOOP_WINDOW_DAYS = 7
_LOCAL_CHANNEL = Provider.INTERACTIVE_SHELL.value


@dataclass(frozen=True)
class ScheduledLoop:
    """The persisted loop and whether it already existed."""

    loop: ManualLoop
    reused: bool

    @property
    def task_id(self) -> str:
        return self.loop.task.id


def loop_name(owner: str, repo: str) -> str:
    return f"CI reliability check · {owner}/{repo}"


def loop_prompt(owner: str, repo: str) -> str:
    """The report request the loop runs unattended on every tick."""
    return (
        f"Scheduled CI/CD reliability report for {owner}/{repo}. First call "
        f'analyze_github_ci_reliability(owner="{owner}", repo="{repo}", days={LOOP_WINDOW_DAYS}); '
        "this read-only tool is the only source of the report. The report body is the tool's "
        "response_text followed by its headline, exactly as returned: do not compute, convert, "
        "reword, or omit any figure, and never answer without the tool result."
    )


def report_looks_complete(report: str, owner: str, repo: str) -> bool:
    """True when a delivered report carries the analytics header for ``owner/repo``."""
    return f"CI/CD reliability for {owner}/{repo}" in report


def schedule_ci_reliability_loop(
    owner: str,
    repo: str,
    *,
    time_text: str = DEFAULT_LOOP_TIME,
    weekdays: bool = True,
    timezone: str = "",
    store_path: Path | None = None,
) -> ScheduledLoop:
    """Create the loop for ``owner/repo``, or return the one that already exists.

    Delivery is pinned to this machine's shell inbox so a scheduled report can
    never post to a chat channel by accident. Raises ``ValueError`` for a time
    the scheduler cannot parse.
    """
    prompt = loop_prompt(owner, repo)
    existing = next(
        (
            task
            for task in list_tasks(store_path)
            if task.kind is TaskKind.MANUAL_LOOP and task.params.get(LOOP_PROMPT_PARAM) == prompt
        ),
        None,
    )
    if existing is not None:
        loop = ManualLoop(
            task=existing,
            channels=loop_channels(existing),
            next_run=existing.next_run,
        )
        return ScheduledLoop(loop=loop, reused=True)
    created = create_manual_loop(
        name=loop_name(owner, repo),
        prompt=prompt,
        time_text=time_text,
        timezone=timezone or local_timezone(),
        weekdays=weekdays,
        channels=(_LOCAL_CHANNEL,),
        store_path=store_path,
    )
    return ScheduledLoop(loop=created, reused=False)


def local_timezone() -> str:
    """This machine's IANA timezone, or UTC when it cannot be resolved."""
    name = local_tz_name()
    try:
        ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        return "UTC"
    return name


def loop_card(scheduled: ScheduledLoop) -> list[str]:
    """Plain lines describing the loop: schedule, next run, where reports land."""
    task = scheduled.loop.task
    verb = "Already scheduled" if scheduled.reused else "Scheduled"
    when = loop_time_label(task.cron) or task.cron
    cadence = "weekdays" if task.cron.split()[-1] == "1-5" else "every day"
    lines = [
        f"{verb}: {task.name}",
        f"Runs {cadence} at {when} {task.timezone}; next run {scheduled.loop.next_run or 'pending'}.",
        "Each report lands in this shell's inbox: /loops messages. "
        f"Manage it with /loops list, /loops stop {task.id}, /loops delete {task.id}.",
        "It runs while the shell is open, or under `opensre cron start` when it is not.",
    ]
    return lines


__all__ = [
    "DEFAULT_LOOP_TIME",
    "LOOP_WINDOW_DAYS",
    "ScheduledLoop",
    "local_timezone",
    "loop_card",
    "loop_name",
    "loop_prompt",
    "report_looks_complete",
    "schedule_ci_reliability_loop",
]
