"""``opensre remote-sync`` — thin Click adapter over :mod:`infrastructure.filestorage`."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TextIO, cast

import click

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
)
from infrastructure.filestorage import RemoteSyncConfigError, RemoteSyncError
from infrastructure.filestorage.enums import RemoteSyncField, RemoteSyncSubcommand
from infrastructure.filestorage.messages import (
    DISABLED_HELP,
    SETUP_DISABLED_CONFIRM,
    format_report_lines,
    format_setup_lines,
    format_status_lines,
)
from infrastructure.filestorage.operations import get_sync_status, run_remote_sync
from infrastructure.filestorage.providers.registry import builtin_providers, provider_extra_fields
from infrastructure.filestorage.setup import (
    RemoteSyncSetupRequest,
    disable_remote_sync,
    save_remote_sync_settings,
)
from infrastructure.process.exit_codes import ERROR, SUCCESS
from surfaces.cli.commands.remote_sync_progress import CliProgress
from surfaces.cli.telemetry import capture_exception


@click.group(name="remote-sync", invoke_without_command=True)
@click.pass_context
def remote_sync_command(ctx: click.Context) -> None:
    """Mirror sessions and memory to your own object store."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(status_command)


@remote_sync_command.command(name=RemoteSyncSubcommand.STATUS.value)
def status_command() -> None:
    """Show whether sync is on, and what would be mirrored."""
    try:
        lines = format_status_lines(get_sync_status())
    except RemoteSyncError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc
    for line in lines:
        click.echo(line)
    raise SystemExit(SUCCESS)


@remote_sync_command.command(name=RemoteSyncSubcommand.SYNC.value)
@click.option("--pull-only", is_flag=True, help="Download only; send nothing.")
@click.option("--push-only", is_flag=True, help="Upload only; fetch nothing.")
@click.option("--dry-run", is_flag=True, help="Preview transfers without changing anything.")
def sync_now_command(pull_only: bool, push_only: bool, dry_run: bool) -> None:
    """Sync now: pull remote changes, then push local ones."""
    stdout = cast(Callable[[str], TextIO], click.get_text_stream)("stdout")
    progress = CliProgress() if stdout.isatty() else None
    try:
        report = run_remote_sync(
            pull_only=pull_only, push_only=push_only, dry_run=dry_run, on_progress=progress
        )
    except RemoteSyncError as exc:
        if progress is not None:
            progress.close()
        click.echo(f"Sync failed: {exc}", err=True)
        raise SystemExit(ERROR) from exc
    finally:
        if progress is not None:
            progress.close()
    if report is None:
        click.echo(DISABLED_HELP)
        raise SystemExit(SUCCESS)
    for line in format_report_lines(report, dry_run=dry_run):
        click.echo(line)
    raise SystemExit(SUCCESS)


def run_remote_sync_on_exit() -> None:
    """Run remote sync without changing the interactive shell's exit result."""
    try:
        report = run_remote_sync()
    except Exception as exc:  # noqa: BLE001 - an optional exit hook must fail soft
        with suppress(Exception):
            capture_exception(exc, context="surfaces.cli.sync_on_exit")
        click.echo(
            "Automatic remote sync failed; run 'opensre remote-sync sync' for details.",
            err=True,
        )
        return

    if report is None:
        click.echo(
            "Automatic remote sync skipped because remote sync is off; "
            "run 'opensre remote-sync setup' first.",
            err=True,
        )
        return
    for line in format_report_lines(report):
        click.echo(line)


@remote_sync_command.command(name=RemoteSyncSubcommand.SETUP.value)
@click.option(
    "--provider",
    default=None,
    help=(
        f"Backend name (default {DEFAULT_REMOTE_SYNC_PROVIDER}; "
        f"built-in: {', '.join(builtin_providers())})."
    ),
)
@click.option(
    "--bucket",
    default=None,
    help="Store name you own (S3 bucket, GCS bucket, or Blob store id).",
)
@click.option(
    "--prefix",
    default=None,
    help=f"Key prefix (default {DEFAULT_REMOTE_SYNC_PREFIX}).",
)
@click.option("--region", default=None, help="Region override when the provider supports it.")
@click.option("--profile", default=None, help="Named credentials profile (AWS).")
@click.option(
    "--enabled/--disabled",
    default=True,
    show_default=True,
    help="Whether remote sync is switched on in stored settings. --disabled alone only turns it off.",
)
def setup_command(
    provider: str | None,
    bucket: str | None,
    prefix: str | None,
    region: str | None,
    profile: str | None,
    enabled: bool,
) -> None:
    """Write remote_sync settings to ~/.opensre/config.yml (interactive if flags omitted)."""
    if not enabled and (bucket is None or not bucket.strip()):
        # --disabled with no new settings just switches the stored section off.
        try:
            _reject_disabled_with_setup_flags(
                provider=provider, prefix=prefix, region=region, profile=profile
            )
            disable_remote_sync()
        except RemoteSyncError as exc:
            click.echo(str(exc), err=True)
            raise SystemExit(ERROR) from exc
        click.echo(SETUP_DISABLED_CONFIRM)
        raise SystemExit(SUCCESS)
    try:
        request = _collect_setup_request(
            provider=provider,
            bucket=bucket,
            prefix=prefix,
            region=region,
            profile=profile,
            enabled=enabled,
        )
        config = save_remote_sync_settings(request)
    except (RemoteSyncError, click.Abort) as exc:
        if isinstance(exc, click.Abort):
            raise SystemExit(ERROR) from exc
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc
    for line in format_setup_lines(config, enabled=request.enabled):
        click.echo(line)
    raise SystemExit(SUCCESS)


def _reject_disabled_with_setup_flags(
    *,
    provider: str | None,
    prefix: str | None,
    region: str | None,
    profile: str | None,
) -> None:
    """``--disabled`` only flips the switch; explicit setup values need a bucket."""
    given = [
        name
        for name, value in (
            ("provider", provider),
            ("prefix", prefix),
            ("region", region),
            ("profile", profile),
        )
        if value is not None and value.strip() != ""
    ]
    if given:
        flags = ", ".join(f"--{name}" for name in given)
        raise RemoteSyncConfigError(
            f"--disabled without --bucket only switches sync off; it cannot also set {flags}. "
            "Pass --bucket to save new settings, or drop the extra flags."
        )


def _collect_setup_request(
    *,
    provider: str | None,
    bucket: str | None,
    prefix: str | None,
    region: str | None,
    profile: str | None,
    enabled: bool,
) -> RemoteSyncSetupRequest:
    """Use flags when complete; otherwise prompt on a TTY."""
    flags_complete = provider is not None and bucket is not None and str(bucket).strip() != ""
    if flags_complete:
        return RemoteSyncSetupRequest(
            bucket=str(bucket),
            provider=str(provider),
            prefix=prefix if prefix is not None else DEFAULT_REMOTE_SYNC_PREFIX,
            region=region or "",
            profile=profile or "",
            enabled=enabled,
        )

    stdin = cast(Callable[[str], TextIO], click.get_text_stream)("stdin")
    if not stdin.isatty():
        raise RemoteSyncError(
            "pass --provider and --bucket, or run setup in an interactive terminal"
        )

    bucket_value = click.prompt(
        "Bucket / store name", default=bucket or "", show_default=bool(bucket)
    )
    provider_value = click.prompt(
        f"Provider ({', '.join(builtin_providers())}, …)",
        default=provider or DEFAULT_REMOTE_SYNC_PROVIDER,
        show_default=True,
    )
    prefix_value = click.prompt(
        "Prefix", default=prefix or DEFAULT_REMOTE_SYNC_PREFIX, show_default=True
    )
    return RemoteSyncSetupRequest(
        bucket=bucket_value,
        provider=provider_value,
        prefix=prefix_value,
        region=_prompt_extra_field(RemoteSyncField.REGION, provider_value, region),
        profile=_prompt_extra_field(RemoteSyncField.PROFILE, provider_value, profile),
        enabled=enabled,
    )


def _prompt_extra_field(field: RemoteSyncField, provider: str, current: str | None) -> str:
    """Prompt for ``field`` only if ``provider`` declared it; otherwise leave it unset.

    The declaration (and its prompt text) lives with the provider — see
    :func:`infrastructure.filestorage.providers.registry.provider_extra_fields` —
    so this stays generic across every registered provider, built-in or
    community, instead of hardcoding which providers use which field.
    """
    declared = {extra.field: extra for extra in provider_extra_fields(provider)}
    extra = declared.get(field)
    if extra is None:
        return current or ""
    return str(click.prompt(extra.prompt, default=current or "", show_default=False))


__all__ = ["remote_sync_command", "run_remote_sync_on_exit"]
