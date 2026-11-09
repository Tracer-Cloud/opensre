"""Canonical Click options shared by cron CLI and interactive input."""

from __future__ import annotations

import click

from core.agent_harness import loop_template_names
from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODES
from infrastructure.scheduling.scheduler.types import Provider, TaskKind

# Sentry-kind tasks are created and listed only through `opensre sentry
# digest`/`opensre sentry uptime watch` (dedicated Sentry-integration setup,
# project_slug handling), not through this generic command group, so they
# are deliberately excluded from --kind here rather than a hand-typed list
# that happens to match.
_CRON_ADD_SUPPORTED_KINDS: tuple[TaskKind, ...] = tuple(
    kind
    for kind in TaskKind
    if kind not in (TaskKind.SENTRY_MORNING_DIGEST, TaskKind.SENTRY_UPTIME_WATCH)
)
_KIND_CHOICES = [k.value for k in _CRON_ADD_SUPPORTED_KINDS]
_PROVIDER_CHOICES = [p.value for p in Provider]


def _reject_generic_work_item_reminder(
    _ctx: click.Context, _param: click.Parameter, kind: str
) -> str:
    """Keep reminders on the work-item creation path that supplies their ID."""
    if kind == TaskKind.WORK_ITEM_REMINDER.value:
        raise click.BadParameter(
            "work_item_reminder tasks must be created with `opensre work add --remind-at`.",
            param_hint="--kind",
        )
    return kind


def cron_add_parameters() -> list[click.Parameter]:
    """Build fresh parameters with the same defaults, types, and callbacks for both surfaces."""
    return [
        click.Option(
            ["--name"],
            type=str,
            default="",
            show_default=False,
            help="Human-readable loop name for list output.",
        ),
        click.Option(
            ["--description"],
            type=str,
            default="",
            show_default=False,
            help="One sentence on what the loop does for its readers, shown when loops are listed.",
        ),
        click.Option(
            ["--kind"],
            type=click.Choice(_KIND_CHOICES, case_sensitive=False),
            required=True,
            callback=_reject_generic_work_item_reminder,
            help="The kind of scheduled task.",
        ),
        click.Option(
            ["--cron", "cron_expr"],
            type=str,
            default="",
            help="Cron expression (5 fields: minute hour day month day_of_week; "
            "prepend a seconds field, e.g. '*/30 * * * * *', for sub-minute polling). "
            "Required unless --template supplies one.",
        ),
        click.Option(
            ["--tz", "timezone"],
            type=str,
            default="UTC",
            show_default=True,
            help="IANA timezone for the schedule (e.g. Europe/London, US/Eastern).",
        ),
        click.Option(
            ["--provider"],
            type=click.Choice(_PROVIDER_CHOICES, case_sensitive=False),
            required=True,
            help="Messaging provider for delivery.",
        ),
        click.Option(
            ["--chat-id"],
            type=str,
            default="",
            show_default=False,
            help="Chat/channel ID for the target provider. Required unless the "
            "provider already has a configured destination, such as a webhook "
            "is configured (the webhook's bound channel is the destination).",
        ),
        click.Option(
            ["--window", "window_hours"],
            type=click.IntRange(min=1),
            default=24,
            show_default=True,
            help="Lookback window in hours for the report (must be >= 1).",
        ),
        click.Option(
            ["--prompt"],
            type=str,
            default="",
            show_default=False,
            help="Instruction to execute on each manual_loop run.",
        ),
        click.Option(
            ["--template"],
            type=click.Choice(loop_template_names()),
            default=None,
            help="Shipped loop template a manual_loop runs instead of --prompt; each tick runs the "
            "template's current text. It also supplies the default name, description, cron and mode.",
        ),
        click.Option(
            ["--mode"],
            type=click.Choice(LOOP_MODES),
            default=None,
            help="Manual-loop behavior: report (default) or agent, which executes the supplied task.",
        ),
        click.Option(
            ["--skill", "skill_name"],
            type=str,
            default="",
            show_default=False,
            help="Skill to run: required for --kind recurring_skill; with --kind manual_loop "
            "--mode agent, the workflow card each tick follows, or the path of an installed "
            "skill folder holding a SKILL.md (--prompt then optional).",
        ),
        click.Option(
            ["--stateless"],
            is_flag=True,
            default=False,
            help="With --kind manual_loop --mode agent: start every run fresh, without earlier "
            "runs, notes for the next run, or long-term memory.",
        ),
        click.Option(["--owner"], type=str, default="", help="GitHub repository owner."),
        click.Option(["--repo"], type=str, default="", help="GitHub repository name."),
        click.Option(["--branch"], type=str, default="", help="Optional GitHub branch filter."),
        click.Option(
            ["--github-connection-id"],
            type=str,
            default="",
            help="App connection used by the GitHub CI report.",
        ),
        click.Option(
            ["--pr", "pr_number"],
            type=click.IntRange(min=1),
            default=None,
            help="Optional GitHub PR filter.",
        ),
        click.Option(
            ["--city"],
            type=str,
            default="",
            help="Optional city for the delivering-morning-briefings skill.",
        ),
    ]
