"""The recurring CI reliability check: a prompt loop that re-runs the analytics for one repository."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from infrastructure.scheduling.scheduler.types import Provider, TaskKind
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
    report_args = {"owner": owner, "repo": repo, "days": str(LOOP_WINDOW_DAYS)}
    existing = next(
        (
            task
            for task in list_tasks(store_path)
            if task.kind is TaskKind.MANUAL_LOOP and task.params.get(LOOP_PROMPT_PARAM) == prompt
        ),
        None,
    )
    if existing is not None:
        if existing.params.get(LOOP_REPORT_PARAM) != REPORT_NAME:
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
    Raises ``RuntimeError`` with a generic message when GitHub cannot be read.
    """
    from integrations.github.client import GitHubApiError, resolve_github_token
    from integrations.github.tools.ci_analytics.analysis import analyze_repository
    from integrations.github.tools.ci_analytics.render import headline, render_markdown
    from integrations.github.tools.ci_analytics.tool import report_payload

    owner = args.get("owner", "").strip()
    repo = args.get("repo", "").strip()
    days = int(args.get("days", LOOP_WINDOW_DAYS) or LOOP_WINDOW_DAYS)
    if not owner or not repo:
        raise RuntimeError("The CI reliability loop needs owner and repo.")
    token = resolve_github_token(None)
    if not token:
        raise RuntimeError(
            f"No GitHub token to read {owner}/{repo}; run `opensre integrations setup github`."
        )
    now = datetime.now(UTC)
    try:
        analysis = analyze_repository(
            owner, repo, token=token, days=days, working_hours=local_working_hours(), now=now
        )
    except (GitHubApiError, ValueError) as exc:
        raise RuntimeError(f"Could not read the GitHub Actions history of {owner}/{repo}.") from exc
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
    return "\n".join([render_markdown(report), "", headline(report), "", f"Raw data: {snapshot}"])


def loop_card(scheduled: ScheduledLoop) -> LoopCard:
    """What the user is told about the loop: one headline and one fact per line."""
    task = scheduled.loop.task
    verb = "Already scheduled" if scheduled.reused else "Scheduled"
    when = loop_time_label(task.cron) or task.cron
    cadence = "weekdays" if task.cron.split()[-1] == "1-5" else "every day"
    return LoopCard(
        headline=f"{verb}: {task.name}",
        details=(
            f"Runs {cadence} at {when} {task.timezone}, next {_next_run_label(scheduled)}",
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
