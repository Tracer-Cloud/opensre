"""Slash ``/remote-sync`` — same service as ``opensre remote-sync`` and gateway ports."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskID, TextColumn

from config.command_inputs import REMOTE_SYNC_SETUP_INPUT
from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PROVIDER,
)
from infrastructure.filestorage import OrgScopeNotSupportedError, RemoteSyncError
from infrastructure.filestorage.engine import SyncProgress
from infrastructure.filestorage.enums import BucketExposure, RemoteSyncSubcommand, SyncDirection
from infrastructure.filestorage.messages import (
    DISABLED_HELP,
    SETUP_DISABLED_CONFIRM,
    direction_label,
    format_exclusion_lines,
    format_exposure_line,
    format_report_lines,
    format_setup_lines,
    root_state,
    sanitize_terminal_text,
)
from infrastructure.filestorage.operations import get_sync_status, run_remote_sync
from infrastructure.filestorage.providers.registry import builtin_providers
from infrastructure.filestorage.setup import (
    RemoteSyncSetupError,
    disable_remote_sync,
    save_remote_sync_settings,
)
from surfaces.interactive_shell.command_registry.input_collection import can_collect_input
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT
from surfaces.interactive_shell.ui.remote_sync_input import build_remote_sync_form, setup_request
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import InputFormError

logger = logging.getLogger(__name__)

_USAGE = (
    f"/remote-sync [{RemoteSyncSubcommand.STATUS}|{RemoteSyncSubcommand.SYNC}|"
    f"{RemoteSyncSubcommand.SETUP}] [--pull-only|--push-only|--dry-run] "
    f"[--provider … --bucket …]"
)


def _print_lines(
    console: Console, lines: tuple[str, ...] | list[str], *, dim_last: bool = False
) -> None:
    for index, line in enumerate(lines):
        if dim_last and index == len(lines) - 1:
            console.print(f"[{DIM}]{line}[/]")
        else:
            console.print(line)


def _print_status(console: Console) -> bool:
    status = get_sync_status()
    if not status.enabled or status.config is None:
        console.print(f"[{DIM}]{DISABLED_HELP}[/]")
        return True
    cfg = status.config
    console.print(f"Remote sync is on ({cfg.provider}) → [{HIGHLIGHT}]{cfg.bucket}/{cfg.prefix}[/]")
    if status.exposure is not None:
        style = ERROR if status.exposure.exposure is BucketExposure.PUBLIC else DIM
        console.print(f"[{style}]{escape(format_exposure_line(status.exposure))}[/]")
    for root in status.roots:
        console.print(f"  {root.name:<10} {root.path} [{DIM}]({root_state(root)})[/]")
    for line in format_exclusion_lines(status.exclusions):
        # Pattern lines carry user-written glob text (character classes like
        # "[a-z]" are ordinary fnmatch syntax), so it must be escaped before
        # it reaches Rich markup — unescaped, "[/]" inside a pattern raises
        # MarkupError and crashes this command on every surface it serves,
        # gateway chat included.
        console.print(f"[{DIM}]{escape(line)}[/]")
    console.print(f"[{DIM}]Never uploaded: integration credentials and model keys.[/]")
    return True


def _run_sync(console: Console, args: list[str]) -> bool:
    flags = {a.lower() for a in args}
    dry_run = "--dry-run" in flags
    bar = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("{task.fields[current]}"),
        console=console,
        transient=True,
    )
    task_id: TaskID | None = None
    current_direction: SyncDirection | None = None

    def _on_progress(progress: SyncProgress) -> None:
        nonlocal task_id, current_direction
        # Rich renders task fields as markup; a session/memory filename is
        # attacker-shaped the same way an exclude pattern is, so it gets the
        # same treatment as _print_status's escape(line) below.
        key_text = escape(sanitize_terminal_text(progress.key))
        if progress.direction is not current_direction:
            current_direction = progress.direction
            label = direction_label(progress.direction)
            if task_id is None:
                task_id = bar.add_task(label, total=progress.total, current=key_text)
            else:
                bar.reset(task_id, total=progress.total, description=label, current=key_text)
        assert task_id is not None
        bar.update(task_id, completed=progress.completed, current=key_text)

    with bar:
        report = run_remote_sync(
            pull_only="--pull-only" in flags,
            push_only="--push-only" in flags,
            dry_run=dry_run,
            on_progress=_on_progress,
        )
    if report is None:
        console.print(f"[{DIM}]{DISABLED_HELP}[/]")
        return True
    lines = format_report_lines(report, dry_run=dry_run)
    _print_lines(console, lines, dim_last=bool(report.kept_remote))
    return True


def _parse_setup_args(args: list[str]) -> dict[str, str]:
    """Reject malformed setup flags before collecting input or changing settings."""
    values: dict[str, str] = {}
    index = 0
    while index < len(args):
        option, separator, value = args[index].partition("=")
        if option in {"--enabled", "--disabled"} and not separator:
            values["enabled"] = "true" if option == "--enabled" else "false"
        elif option in REMOTE_SYNC_SETUP_INPUT.value_options:
            if not separator:
                index += 1
                if index >= len(args) or args[index].startswith("--"):
                    raise ValueError(f"{option} requires a value")
                value = args[index]
            if value.startswith("--"):
                raise ValueError(f"{option} requires a value")
            values[option[2:]] = value
        else:
            raise ValueError(f"Unknown setup option: {option!r}")
        index += 1
    return values


def _validate_setup_args(args: list[str]) -> str | None:
    if args and args[0].lower() == "setup":
        try:
            _parse_setup_args(args[1:])
        except ValueError as exc:
            return escape(str(exc))
    return None


def _run_setup(session: Session, console: Console, args: list[str]) -> bool:
    """Collect a terminal draft or save complete gateway-safe setup flags."""
    values = _parse_setup_args(args)
    if values.get("enabled") == "false" and not values.get("bucket", "").strip():
        extras = [name for name in values if name != "enabled" and values[name].strip()]
        if extras:
            console.print(
                "--disabled without --bucket only switches sync off; "
                "pass --bucket to save new settings, or drop the extra flags."
            )
            return True
        disable_remote_sync()
        console.print(SETUP_DISABLED_CONFIRM)
        return True
    if can_collect_input(session, REMOTE_SYNC_SETUP_INPUT, ["setup", *args]):
        request = run_command_input(build_remote_sync_form(values))
        if request is None:
            return True
    elif not values.get("bucket", "").strip():
        console.print(
            f"[{ERROR}]setup needs --bucket[/]. "
            "Example: [bold]/remote-sync setup --provider vercel "
            "--bucket opensre-remote-sync[/]\n"
            "In an interactive terminal, run /remote-sync setup to open the editable form."
        )
        return True
    else:
        request = setup_request(values)
    config = save_remote_sync_settings(request)
    for line in format_setup_lines(config, enabled=request.enabled):
        console.print(escape(line))
    return True


def _parse_subcommand(raw: str) -> RemoteSyncSubcommand | None:
    try:
        return RemoteSyncSubcommand(raw)
    except ValueError:
        return None


def _cmd_remote_sync(_session: Session, console: Console, args: list[str]) -> bool:
    token = (args[0] if args else RemoteSyncSubcommand.STATUS.value).strip().lower()
    sub = RemoteSyncSubcommand.STATUS if not token else _parse_subcommand(token)
    try:
        if sub is RemoteSyncSubcommand.STATUS:
            return _print_status(console)
        if sub is RemoteSyncSubcommand.SYNC:
            return _run_sync(console, args[1:])
        if sub is RemoteSyncSubcommand.SETUP:
            return _run_setup(_session, console, args[1:])
    except (RemoteSyncSetupError, InputFormError) as exc:
        console.print(f"[{ERROR}]{escape(str(exc))}[/]")
        return True
    except OrgScopeNotSupportedError as exc:
        # Safe to show: our own wording, no vendor or credential detail.
        console.print(f"[{DIM}]{exc}[/]")
        return True
    except RemoteSyncError:
        # This handler also serves gateway chat, an external surface, so the
        # provider's message never reaches the reply. Detail goes to the log.
        logger.warning("[remote-sync] command failed", exc_info=True)
        console.print(
            f"[{ERROR}]Remote-sync command failed.[/] Check the opensre log, "
            "or run [bold]opensre remote-sync status[/bold] locally for detail."
        )
        return True
    console.print(
        f"[{ERROR}]unknown subcommand:[/] {token}  "
        f"(try [bold]/remote-sync {RemoteSyncSubcommand.STATUS}[/bold])"
    )
    return True


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        "/remote-sync",
        "Mirror your sessions and memory to your own object store.",
        _cmd_remote_sync,
        usage=(_USAGE,),
        validate_args=_validate_setup_args,
        notes=(
            "Off until setup or OPENSRE_REMOTE_SYNC is set. "
            f"Subcommands: status, sync, setup. Default provider is "
            f"{DEFAULT_REMOTE_SYNC_PROVIDER} (built-in: {', '.join(builtin_providers())}). "
            "Credentials stay ambient; integration keys are never uploaded. "
            "Setup opens an editable form in the REPL; external CLI and gateways use flags. "
            "Complete flags save without prompting. Same service as `opensre remote-sync`.",
        ),
        first_arg_completions=(
            (
                RemoteSyncSubcommand.STATUS.value,
                "show whether sync is on and what would be mirrored",
            ),
            (RemoteSyncSubcommand.SYNC.value, "pull remote changes, then push local ones"),
            (
                RemoteSyncSubcommand.SETUP.value,
                "edit remote-sync settings, or save complete setup flags",
            ),
        ),
        use_cases=(
            "set up remote sync to Vercel Blob",
            "sync my conversations to S3",
            "back up my opensre memory",
            "set up opensre on my second laptop",
        ),
    ),
)

__all__ = ["COMMANDS"]
