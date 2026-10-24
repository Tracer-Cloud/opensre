"""Slash ``/remote-sync`` — same service as ``opensre remote-sync`` and gateway ports."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskID, TextColumn

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
)
from infrastructure.filestorage import OrgScopeNotSupportedError, RemoteSyncError
from infrastructure.filestorage.engine import SyncProgress
from infrastructure.filestorage.enums import BucketExposure, RemoteSyncSubcommand, SyncDirection
from infrastructure.filestorage.messages import (
    DISABLED_HELP,
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
from infrastructure.filestorage.setup import RemoteSyncSetupRequest, save_remote_sync_settings
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT

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


def _flag_value(args: list[str], name: str) -> str | None:
    """Return ``--name value`` from ``args``, or None.

    Returns None when the next token is missing or starts with ``--``
    (another flag), matching the validation in loops_cmds._take_flag_value.
    """
    token = f"--{name}"
    for index, arg in enumerate(args):
        if arg == token:
            if index + 1 < len(args) and not args[index + 1].startswith("--"):
                return args[index + 1]
            return None
        if arg.startswith(f"{token}="):
            return arg.split("=", 1)[1]
    return None


def _flag_present(args: list[str], name: str) -> bool:
    """Return True when ``--name`` appears in ``args`` (with or without ``=value``)."""
    token = f"--{name}"
    return any(a == token or a.startswith(f"{token}=") for a in args)


def _run_setup(console: Console, args: list[str]) -> bool:
    """Write stored settings from flags (gateway-safe: no interactive prompts)."""
    for flag in ("provider", "bucket", "prefix", "region", "profile"):
        if _flag_present(args, flag) and _flag_value(args, flag) is None:
            console.print(f"[{ERROR}]--{flag} requires a value[/].")
            return True
    provider = _flag_value(args, "provider") or DEFAULT_REMOTE_SYNC_PROVIDER
    bucket = _flag_value(args, "bucket")
    if not bucket or not bucket.strip():
        console.print(
            f"[{ERROR}]setup needs --bucket[/]. "
            "Example: [bold]/remote-sync setup --provider vercel "
            "--bucket opensre-remote-sync[/]\n"
            f"[{DIM}]Interactive prompts: opensre remote-sync setup[/]"
        )
        return True
    prefix = _flag_value(args, "prefix") or DEFAULT_REMOTE_SYNC_PREFIX
    region = _flag_value(args, "region") or ""
    profile = _flag_value(args, "profile") or ""
    enabled = "--disabled" not in {a.lower() for a in args}
    config = save_remote_sync_settings(
        RemoteSyncSetupRequest(
            bucket=bucket,
            provider=provider,
            prefix=prefix,
            region=region,
            profile=profile,
            enabled=enabled,
        )
    )
    _print_lines(console, format_setup_lines(config, enabled=enabled))
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
            return _run_setup(console, args[1:])
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
        notes=(
            "Off until setup or OPENSRE_REMOTE_SYNC is set. "
            f"Subcommands: status, sync, setup. Default provider is "
            f"{DEFAULT_REMOTE_SYNC_PROVIDER} (built-in: {', '.join(builtin_providers())}). "
            "Credentials stay ambient; integration keys are never uploaded. "
            "Same service as `opensre remote-sync`.",
        ),
        first_arg_completions=(
            (
                RemoteSyncSubcommand.STATUS.value,
                "show whether sync is on and what would be mirrored",
            ),
            (RemoteSyncSubcommand.SYNC.value, "pull remote changes, then push local ones"),
            (
                RemoteSyncSubcommand.SETUP.value,
                "save provider/bucket/prefix to ~/.opensre/config.yml",
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
