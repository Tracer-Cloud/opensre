"""The recurring CI reliability check: a prompt loop that re-runs the analytics for one repository."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.constants.scheduler import WEEKDAY_CRON_FIELD
from infrastructure.scheduling.scheduler.cron_expression import day_of_week_names
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_PROMPT_PARAM,
    LOOP_REPORT_ARGS_PARAM,
    LOOP_REPORT_PARAM,
)
from infrastructure.scheduling.scheduler.loops import (
    ManualLoop,
    create_manual_loop,
    loop_channels,
    loop_time_label,
)
from infrastructure.scheduling.scheduler.storage import list_tasks, update_task
from infrastructure.scheduling.scheduler.types import Provider, TaskKind, TaskReport
from integrations.github.tools.ci_analytics.snapshots import (
    SNAPSHOT_DIRNAME,
    report_to_dict,
    snapshot_root,
    write_snapshot,
)
from integrations.github.tools.ci_analytics.working_hours import (
    local_timezone,
    local_working_hours,
)

logger = logging.getLogger(__name__)

DEFAULT_LOOP_TIME = "08:00"
LOOP_WINDOW_DAYS = 7
REPORT_NAME = "github_ci_reliability"
"""Builder name the manual-loop runner maps to :func:`build_report`."""

_LOCAL_CHANNEL = Provider.INTERACTIVE_SHELL.value


@dataclass(frozen=True)
class ScheduledLoop:
    """The persisted loop and whether it already existed."""

    loop: ManualLoop
    reused: bool

    @property
    def task_id(self) -> str:
        return self.loop.task.id


@dataclass(frozen=True)
class LoopCard:
    """The scheduled loop as the user reads it: a headline and short facts."""

    headline: str
    details: tuple[str, ...]

    def markdown(self) -> str:
        """The card as markdown: bold headline, one bullet per fact."""
        bullets = "\n".join(f"- {detail}" for detail in self.details)
        return f"**{self.headline}**\n\n{bullets}"


def loop_name(owner: str, repo: str) -> str:
    return f"CI reliability check · {owner}/{repo}"


def loop_prompt(owner: str, repo: str) -> str:
    """The report request the loop runs unattended on every tick."""
    return (
        f"Scheduled CI/CD reliability report for {owner}/{repo}. First call "
        f'analyze_github_ci_reliability(owner="{owner}", repo="{repo}", days={LOOP_WINDOW_DAYS}); '
        "this read-only tool is the only source of the report. The report body is the tool's "
        "response_text exactly as returned: do not compute, convert, reword, or omit any "
        "figure, do not add the headline, and never answer without the tool result."
    )


def report_looks_complete(report: str, owner: str, repo: str) -> bool:
    """True when a delivered report carries the analytics header for ``owner/repo``."""
    return f"CI/CD reliability for {owner}/{repo}" in report


def schedule_ci_reliability_loop(
    owner: str,
    repo: str,
    *,
    time_text: str = DEFAULT_LOOP_TIME,
    github_connection_id: str = "",
    weekdays: bool = True,
    timezone: str = "",
    store_path: Path | None = None,
) -> ScheduledLoop:
    """Create the loop for ``owner/repo``, or return the one that already exists.

    A loop saved before the deterministic builder existed is upgraded in
    place so it stops running as a model turn. Delivery is pinned to this
    machine's shell inbox so a scheduled report can never post to a chat
    channel by accident. Raises ``ValueError`` for a time the scheduler
    cannot parse.
    """
    prompt = loop_prompt(owner, repo)
    report_args = {
        "owner": owner,
        "repo": repo,
        "days": str(LOOP_WINDOW_DAYS),
    }
    if github_connection_id:
        report_args["github_connection_id"] = github_connection_id
    existing = next(
        (
            task
            for task in list_tasks(store_path)
            if task.kind is TaskKind.MANUAL_LOOP and task.params.get(LOOP_PROMPT_PARAM) == prompt
        ),
        None,
    )
    if existing is not None:
        if existing.params.get(LOOP_REPORT_PARAM) != REPORT_NAME or existing.params.get(
            LOOP_REPORT_ARGS_PARAM
        ) != json.dumps(report_args, sort_keys=True):
            existing.params[LOOP_REPORT_PARAM] = REPORT_NAME
            existing.params[LOOP_REPORT_ARGS_PARAM] = json.dumps(report_args, sort_keys=True)
            update_task(existing, store_path)
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
        report=REPORT_NAME,
        report_args=report_args,
    )
    return ScheduledLoop(loop=created, reused=False)


def build_report(args: Mapping[str, str], *, snapshot_dir: Path | None = None) -> str:
    """Deterministic loop tick: read GitHub, render the report, keep the raw figures on disk.

    No model is involved, so every delivery carries the analytics header and
    the numbers can be traced back to the JSON snapshot named at the end.
    When GitHub cannot be read, returns a blocked report naming what stopped
    the read, never exception detail, so the loop's channels hear it.
    Raises ``RuntimeError`` only for a loop without a repository or a token.
    """
    from integrations.github.client import (
        GitHubApiError,
        github_failure_kind,
    )
    from integrations.github.tools.ci_analytics.analysis import analyze_repository
    from integrations.github.tools.ci_analytics.failure import (
        analysis_failure_report,
        is_operational_failure,
    )
    from integrations.github.tools.ci_analytics.payload import report_payload
    from integrations.github.tools.ci_analytics.render import headline, render_markdown

    owner = args.get("owner", "").strip()
    repo = args.get("repo", "").strip()
    days = int(args.get("days", LOOP_WINDOW_DAYS) or LOOP_WINDOW_DAYS)
    if not owner or not repo:
        raise RuntimeError("The CI reliability loop needs owner and repo.")
    from integrations.github.app_connection import github_setup_url, refreshed_github_token

    token = refreshed_github_token(args.get("github_connection_id"))
    if not token:
        return TaskReport(
            f"CI analysis blocked for {owner}/{repo}. Connect or reconnect GitHub in the OpenSRE app: {github_setup_url()}",
            work_status="blocked",
            error_kind="github_connection_required",
        )
    now = datetime.now(UTC)
    try:
        analysis = analyze_repository(
            owner, repo, token=token, days=days, working_hours=local_working_hours(), now=now
        )
    except (GitHubApiError, ValueError) as exc:
        # Same split as the tool: GitHub, the network or the token is a
        # warning without a stack; anything else is a fault worth one.
        if is_operational_failure(exc):
            kind = github_failure_kind(exc).value
            logger.warning("CI reliability loop could not read %s/%s: %s", owner, repo, kind)
        else:
            logger.error("CI reliability loop could not read %s/%s", owner, repo, exc_info=exc)
        return analysis_failure_report(exc, owner=owner, repo=repo, now=datetime.now(UTC))
    report = analysis.report
    snapshot = write_snapshot(
        snapshot_root(snapshot_dir),
        owner,
        repo,
        now,
        {
            "generated_at": now.isoformat(),
            "window_days": days,
            "headline": headline(report),
            "report": report_to_dict(report),
            **report_payload(report),
        },
    )
    summary = (
        headline(report)
        if report.executions
        else "No completed workflow runs were found in this window."
    )
    return TaskReport(
        "\n".join([render_markdown(report), "", f"Raw data: {snapshot}"]), summary=summary
    )


def loop_card(scheduled: ScheduledLoop) -> LoopCard:
    """What the user is told about the loop: one headline and one fact per line."""
    task = scheduled.loop.task
    verb = "Already scheduled" if scheduled.reused else "Scheduled"
    # Empty unless the cron is five fields with a plain hour and minute.
    when = loop_time_label(task.cron)
    try:
        days = day_of_week_names(task.cron.split()[-1])
    except ValueError:
        days = ""
    if when and days == WEEKDAY_CRON_FIELD:
        schedule = f"weekdays at {when}"
    elif when and days == "*":
        schedule = f"every day at {when}"
    else:
        schedule = f"on cron {task.cron}"
    return LoopCard(
        headline=f"{verb}: {task.name}",
        details=(
            f"Runs {schedule} {task.timezone}, next {_next_run_label(scheduled)}",
            "Reports arrive in this shell's inbox: `/loops messages`",
            f"Manage: `/loops list`, `/loops stop {task.id}`, "
            f"`/loops delete {task.id}` (delete to reschedule)",
            "Runs while the shell is open; `opensre cron start` keeps it running when it is not",
        ),
    )


def _next_run_label(scheduled: ScheduledLoop) -> str:
    """The next run in the schedule's own timezone, so it matches the time asked for."""
    raw = scheduled.loop.next_run
    if not raw:
        return "pending"
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    try:
        when = when.astimezone(ZoneInfo(scheduled.loop.task.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        when = when.astimezone()
    return when.strftime("%a %d %b %H:%M")


__all__ = [
    "DEFAULT_LOOP_TIME",
    "LOOP_WINDOW_DAYS",
    "LoopCard",
    "REPORT_NAME",
    "SNAPSHOT_DIRNAME",
    "ScheduledLoop",
    "build_report",
    "local_timezone",
    "loop_card",
    "loop_name",
    "loop_prompt",
    "report_looks_complete",
    "schedule_ci_reliability_loop",
]
