"""Slash commands for scheduled loops."""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console
from rich.markup import escape

from infrastructure.scheduling.scheduler.loop_constants import LOOP_TIME_PARAM
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    print_repl_table,
    repl_table,
)
from surfaces.shared.terminal.components.time_format import format_repl_timestamp

_LOOPS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "all active and draft loops"),
    ("active", "only enabled loops"),
    ("all", "active and draft loops"),
    ("add", "create a recurring prompt loop"),
    ("run", "execute one loop immediately"),
    ("stop", "disable a loop without deleting it"),
    ("start", "enable a stopped or draft loop"),
    ("delete", "remove a loop permanently"),
    ("next", "debug one loop's next fire time"),
    ("messages", "local interactive-shell loop inbox"),
    ("service", "background scheduler service: status, install, remove"),
)

_USAGE = (
    "/loops",
    "/loops active",
    "/loops add --name NAME --time HH:MM --prompt PROMPT [--run-now]",
    "/loops run LOOP_ID",
    "/loops stop LOOP_ID",
    "/loops start LOOP_ID",
    "/loops delete LOOP_ID",
    "/loops next LOOP_ID",
    "/loops messages [--limit N]",
    "/loops service [install|remove]",
)


@dataclass(slots=True)
class _AddLoopArgs:
    name: str = ""
    prompt: str = ""
    time_text: str = ""
    timezone: str = "UTC"
    cron: str = ""
    weekdays: bool = False
    run_now: bool = False
    channels: list[str] = field(default_factory=list)
    telegram_chat_id: str = ""
    slack_chat_id: str = ""
    window_hours: int = 24


def _channel_label(channel: str) -> str:
    return channel.replace("_", " ")


def _channels_label(channels: tuple[str, ...]) -> str:
    return ", ".join(_channel_label(channel) for channel in channels)


def _schedule_label(cron: str, timezone: str) -> str:
    return f"{cron} ({timezone})"


def _short(text: str, *, max_chars: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 1].rstrip()}…"


def _loops_usage_error() -> str:
    return (
        f"[{ERROR}]usage:[/] "
        "/loops [list|active|all|add|run|stop|start|delete|next|messages|service]\n"
        f'[{DIM}]example:[/] /loops add --name "Morning ops" --time 08:30 '
        '--prompt "Check open incidents and summarize risk" --run-now'
    )


def _take_flag_value(args: list[str], index: int, flag: str) -> tuple[str, int, str]:
    next_index = index + 1
    if next_index >= len(args) or args[next_index].startswith("--"):
        return "", index, f"{flag} requires a value"
    return args[next_index], next_index, ""


def _take_text_value(args: list[str], index: int, flag: str) -> tuple[str, int, str]:
    next_index = index + 1
    values: list[str] = []
    while next_index < len(args) and not args[next_index].startswith("--"):
        values.append(args[next_index])
        next_index += 1
    if not values:
        return "", index, f"{flag} requires a value"
    return " ".join(values), next_index - 1, ""


def _parse_add_args(args: list[str]) -> tuple[_AddLoopArgs | None, str]:
    parsed = _AddLoopArgs()
    index = 0
    while index < len(args):
        flag = args[index]
        if flag == "--name":
            parsed.name, index, error = _take_text_value(args, index, flag)
        elif flag == "--prompt":
            parsed.prompt, index, error = _take_text_value(args, index, flag)
        elif flag == "--time":
            parsed.time_text, index, error = _take_flag_value(args, index, flag)
        elif flag == "--tz":
            parsed.timezone, index, error = _take_flag_value(args, index, flag)
        elif flag == "--cron":
            parsed.cron, index, error = _take_text_value(args, index, flag)
        elif flag in {"--channel", "--channels"}:
            value, index, error = _take_flag_value(args, index, flag)
            parsed.channels.append(value)
        elif flag == "--telegram-chat-id":
            parsed.telegram_chat_id, index, error = _take_flag_value(args, index, flag)
        elif flag == "--slack-chat-id":
            parsed.slack_chat_id, index, error = _take_flag_value(args, index, flag)
        elif flag == "--window":
            value, index, error = _take_flag_value(args, index, flag)
            if not error:
                try:
                    parsed.window_hours = int(value)
                except ValueError:
                    return None, "--window must be an integer"
        elif flag == "--weekdays":
            parsed.weekdays = True
            error = ""
        elif flag == "--daily":
            parsed.weekdays = False
            error = ""
        elif flag == "--run-now":
            parsed.run_now = True
            error = ""
        else:
            return None, f"unknown option {flag!r}"

        if error:
            return None, error
        index += 1

    return parsed, ""


def _validate_loops_args(args: list[str]) -> str | None:
    if not args:
        return None
    sub = args[0].lower()
    if sub in {
        "active",
        "add",
        "all",
        "debug",
        "delete",
        "disable",
        "enable",
        "inbox",
        "list",
        "messages",
        "next",
        "pause",
        "remove",
        "resume",
        "run",
        "service",
        "start",
        "stop",
    }:
        return None
    return _loops_usage_error()


def _cmd_loops(session: Session, console: Console, args: list[str]) -> bool:
    sub = args[0].lower() if args else "list"
    rest = args[1:] if args else []
    if sub in {"list", "all", "active"}:
        return _cmd_loops_list(session, console, args)
    if sub == "add":
        return _cmd_loops_add(session, console, rest)
    if sub == "run":
        return _cmd_loops_run(session, console, rest)
    if sub in {"stop", "pause", "disable"}:
        return _cmd_loops_set_enabled(session, console, rest, enabled=False)
    if sub in {"start", "resume", "enable"}:
        return _cmd_loops_set_enabled(session, console, rest, enabled=True)
    if sub in {"delete", "remove"}:
        return _cmd_loops_delete(session, console, rest)
    if sub in {"next", "debug"}:
        return _cmd_loops_next(session, console, rest)
    if sub in {"messages", "inbox"}:
        return _cmd_loops_messages(session, console, rest)
    if sub == "service":
        return _cmd_loops_service(session, console, rest)

    console.print(_loops_usage_error())
    return True


def _cmd_loops_list(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from infrastructure.scheduling.scheduler.loops import list_loop_summaries

    sub = args[0].lower() if args else "list"
    include_disabled = sub != "active"
    loops = list_loop_summaries(include_disabled=include_disabled)
    if not loops:
        if include_disabled:
            console.print(
                f"[{DIM}]no loops configured yet. Run[/] "
                f"[{HIGHLIGHT}]opensre onboard[/] [{DIM}]or create one with[/] "
                f"[{HIGHLIGHT}]/loops add[/][{DIM}].[/]"
            )
        else:
            console.print(
                f"[{DIM}]no active loops. Run[/] [{HIGHLIGHT}]/loops all[/] "
                f"[{DIM}]to see draft starter loops.[/]"
            )
        return True

    table = repl_table(title="Loops\n", title_style=BOLD_BRAND)
    table.add_column("id", style="bold")
    table.add_column("loop")
    table.add_column("status")
    table.add_column("time", style=DIM)
    table.add_column("schedule", style=DIM, overflow="fold")
    table.add_column("next", style=DIM)
    table.add_column("channels", style=DIM, overflow="fold")
    table.add_column("last run", style=DIM)

    for loop in loops:
        if loop.schedule_error:
            status_style = ERROR
            status = "invalid"
            next_run = "-"
        else:
            status_style = HIGHLIGHT if loop.enabled else WARNING
            status = loop.status
            next_run = format_repl_timestamp(loop.next_run, style="utc")
        table.add_row(
            loop.id[:12],
            escape(loop.name),
            f"[{status_style}]{status}[/]",
            escape(loop.time or "-"),
            escape(_schedule_label(loop.cron, loop.timezone)),
            next_run,
            escape(_channels_label(loop.channels)),
            format_repl_timestamp(loop.last_run, style="utc"),
        )

    print_repl_table(console, table)
    return True


def _cmd_loops_add(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from infrastructure.scheduling.scheduler.loops import create_manual_loop

    parsed, parse_error = _parse_add_args(args)
    if parsed is None:
        console.print(f"[{ERROR}]could not add loop:[/] {escape(parse_error)}")
        console.print(_loops_usage_error())
        return True

    try:
        created = create_manual_loop(
            name=parsed.name,
            prompt=parsed.prompt,
            time_text=parsed.time_text,
            timezone=parsed.timezone,
            weekdays=parsed.weekdays,
            cron=parsed.cron,
            channels=parsed.channels or None,
            telegram_chat_id=parsed.telegram_chat_id,
            slack_chat_id=parsed.slack_chat_id,
            window_hours=parsed.window_hours,
        )
    except ValueError as exc:
        console.print(f"[{ERROR}]could not add loop:[/] {escape(str(exc))}")
        return True

    console.print(f"[{HIGHLIGHT}]loop created:[/] {escape(created.task.name)}")
    console.print(f"  id: [{HIGHLIGHT}]{created.task.id}[/]")
    console.print(
        f"  time: {created.task.params.get(LOOP_TIME_PARAM) or '-'}  tz: {created.task.timezone}"
    )
    console.print(f"  cron: {escape(created.task.cron)}")
    console.print(f"  next: {format_repl_timestamp(created.next_run, style='utc')}")
    console.print(
        "  channels: "
        + escape(_channels_label(tuple(provider.value for provider in created.channels)))
    )
    _reload_loop_scheduler(console)

    if parsed.run_now:
        _run_loop_task_ids_once(console, (created.task.id,))
    return True


def _cmd_loops_run(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from infrastructure.scheduling.scheduler.loops import resolve_loop_summary

    if not args:
        console.print(f"[{ERROR}]usage:[/] /loops run LOOP_ID")
        return True
    loop, error = resolve_loop_summary(args[0])
    if loop is None:
        console.print(f"[{ERROR}]{escape(error)}[/]")
        return True
    _run_loop_task_ids_once(console, loop.task_ids)
    return True


def _cmd_loops_set_enabled(
    _session: Session,
    console: Console,
    args: list[str],
    *,
    enabled: bool,
) -> bool:  # noqa: ARG001
    from infrastructure.scheduling.scheduler.loops import set_loop_enabled

    verb = "start" if enabled else "stop"
    if not args:
        console.print(f"[{ERROR}]usage:[/] /loops {verb} LOOP_ID")
        return True

    result, error = set_loop_enabled(args[0], enabled=enabled)
    if result is None:
        console.print(f"[{ERROR}]{escape(error)}[/]")
        return True

    action = "started" if enabled else "stopped"
    console.print(f"[{HIGHLIGHT}]loop {action}:[/] {escape(result.summary.name)}")
    console.print(f"  id: [{HIGHLIGHT}]{escape(result.summary.id)}[/]")
    console.print(f"  tasks: {result.task_count}")
    _reload_loop_scheduler(console)
    return True


def _cmd_loops_delete(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from infrastructure.scheduling.scheduler.loops import delete_loop

    if not args:
        console.print(f"[{ERROR}]usage:[/] /loops delete LOOP_ID")
        return True

    result, error = delete_loop(args[0])
    if result is None:
        console.print(f"[{ERROR}]{escape(error)}[/]")
        return True

    console.print(f"[{HIGHLIGHT}]loop deleted:[/] {escape(result.summary.name)}")
    console.print(f"  id: [{HIGHLIGHT}]{escape(result.summary.id)}[/]")
    console.print(f"  tasks removed: {result.task_count}")
    _reload_loop_scheduler(console)
    return True


def _cmd_loops_next(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from infrastructure.scheduling.scheduler.loops import resolve_loop_summary

    if not args:
        console.print(f"[{ERROR}]usage:[/] /loops next LOOP_ID")
        return True
    loop, error = resolve_loop_summary(args[0])
    if loop is None:
        console.print(f"[{ERROR}]{escape(error)}[/]")
        return True

    console.print(f"[{BOLD_BRAND}]Loop:[/] {escape(loop.name)} [{DIM}]{loop.id}[/]")
    console.print(f"  status: {loop.status}")
    console.print(f"  time: {escape(loop.time or '-')}")
    console.print(f"  cron: {escape(loop.cron)}")
    console.print(f"  timezone: {escape(loop.timezone)}")
    console.print(f"  next run: {format_repl_timestamp(loop.next_run, style='utc')}")
    console.print(f"  channels: {escape(_channels_label(loop.channels))}")
    if loop.prompt:
        console.print(f"  prompt: {escape(_short(loop.prompt))}")
    if loop.schedule_error:
        console.print(f"  [{ERROR}]schedule error:[/] {escape(loop.schedule_error)}")
    return True


def _cmd_loops_messages(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from infrastructure.scheduling.scheduler.local_delivery import get_loop_messages

    limit = 20
    if args:
        if len(args) == 2 and args[0] == "--limit":
            try:
                limit = int(args[1])
            except ValueError:
                console.print(f"[{ERROR}]--limit must be an integer[/]")
                return True
        else:
            console.print(f"[{ERROR}]usage:[/] /loops messages [--limit N]")
            return True

    messages = get_loop_messages(limit=limit)
    if not messages:
        console.print(f"[{DIM}]no local loop messages yet.[/]")
        return True

    table = repl_table(title="Loop Messages\n", title_style=BOLD_BRAND)
    table.add_column("created", style=DIM)
    table.add_column("loop")
    table.add_column("id", style=DIM)
    table.add_column("message", overflow="fold")
    for message in messages:
        table.add_row(
            format_repl_timestamp(message.created_at, style="utc"),
            escape(message.name or message.loop_id),
            escape(message.message_id),
            escape(_short(message.message)),
        )
    print_repl_table(console, table)
    return True


def _cmd_loops_service(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from infrastructure.scheduling.scheduler.background_service import (
        background_service_state,
        install_background_service,
        remove_background_service,
    )

    action = args[0].lower() if args else "status"
    try:
        if action == "install":
            state = install_background_service()
        elif action == "remove":
            state = remove_background_service()
        elif action == "status":
            state = background_service_state()
        else:
            console.print(f"[{ERROR}]usage:[/] /loops service [install|remove]")
            return True
    except RuntimeError as exc:
        console.print(f"[{ERROR}]service change failed:[/] {escape(str(exc))}")
        return True
    console.print(escape(state.summary))
    if state.installed and state.log_path is not None:
        console.print(f"  [{DIM}]log:[/] {escape(str(state.log_path))}")
    return True


def _run_loop_task_ids_once(console: Console, task_ids: tuple[str, ...]) -> bool:
    from surfaces.interactive_shell.runtime.loop_scheduler import run_loop_now

    failures = [task_id for task_id in task_ids if not run_loop_now(task_id)]

    if failures:
        console.print(
            f"[{ERROR}]run-now failed for:[/] {escape(', '.join(failures))} "
            f"[{DIM}](check /cron logs)[/]"
        )
        return False
    console.print(f"[{HIGHLIGHT}]run-now complete.[/]")
    return True


def _reload_loop_scheduler(console: Console) -> None:
    from surfaces.interactive_shell.runtime.loop_scheduler import reload_loop_scheduler

    try:
        task_count = reload_loop_scheduler()
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [{WARNING}]scheduler reload failed:[/] {escape(str(exc))}")
        return
    if task_count:
        console.print(f"  scheduler: running {task_count} loop(s)")
    else:
        console.print("  scheduler: idle")


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/loops",
        "List, create, run, and debug recurring prompt loops.",
        _cmd_loops,
        usage=_USAGE,
        examples=(
            '/loops add --name "Morning ops" --time 08:30 --prompt '
            '"Check open incidents and summarize risk" --run-now',
            "/loops stop abc123",
            "/loops delete abc123",
            "/loops next abc123",
        ),
        first_arg_completions=_LOOPS_FIRST_ARGS,
        validate_args=_validate_loops_args,
    ),
]

__all__ = ["COMMANDS", "_LOOPS_FIRST_ARGS"]
