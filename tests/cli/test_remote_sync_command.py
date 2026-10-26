"""CLI adapter for ``opensre remote-sync`` — thin layer over the shared service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from config.constants.filestorage import REMOTE_SYNC_BUCKET_ENV, REMOTE_SYNC_ENV
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.engine import SyncProgress, SyncReport
from infrastructure.filestorage.enums import (
    BuiltInProvider,
    RemoteSyncSubcommand,
    SyncDirection,
    SyncRootName,
)
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.operations import SyncRootStatus, SyncStatus
from infrastructure.filestorage.setup import RemoteSyncSetupRequest
from surfaces.cli.commands.remote_sync import remote_sync_command


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_remote_sync_help_lists_status_and_sync(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["--help"])
    assert result.exit_code == 0
    assert RemoteSyncSubcommand.STATUS in result.output
    assert RemoteSyncSubcommand.SYNC in result.output


def test_status_explains_off_when_disabled(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)

    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_status_shows_provider_when_enabled(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = SyncStatus(
        config=RemoteSyncConfig(bucket="my-bucket", provider=BuiltInProvider.AWS, prefix="opensre"),
        roots=(
            SyncRootStatus(name=SyncRootName.SESSIONS, path=Path("/tmp/sessions"), exists=True),
            SyncRootStatus(name=SyncRootName.MEMORY, path=Path("/tmp/memory"), exists=False),
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.get_sync_status",
        lambda: status,
    )

    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code == 0
    assert "Remote sync is on (aws)" in result.output
    assert "my-bucket/opensre" in result.output
    assert "sessions" in result.output
    assert "not created yet" in result.output


def test_sync_when_disabled_prints_help(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: None,
    )
    result = runner.invoke(remote_sync_command, ["sync"])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_sync_prints_report(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    report = SyncReport(uploaded=["sessions/a.jsonl"], downloaded=[], skipped=1)
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: report,
    )
    result = runner.invoke(remote_sync_command, ["sync"])
    assert result.exit_code == 0
    assert "1 uploaded" in result.output
    assert "already current" in result.output


def test_sync_creates_progress_bars_on_a_tty(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TTY gets a real bar per direction, driven end-to-end without crashing.

    Click hides bar frames (percent, current item) when the underlying
    stream isn't a real TTY — true for CliRunner's captured output even
    after faking ``isatty()`` for our own gate — falling back to printing
    each bar's label once. That fallback is what this asserts: the wiring
    reaches Click's ProgressBar and survives a direction change intact.
    """
    _set_interactive(monkeypatch)

    def _fake_run_remote_sync(
        *,
        pull_only: bool = False,
        push_only: bool = False,
        dry_run: bool = False,
        on_progress: Callable[[SyncProgress], None] | None = None,
    ) -> SyncReport:
        assert on_progress is not None
        on_progress(SyncProgress(SyncDirection.PULL, "memory/a.md", 1, 1))
        on_progress(SyncProgress(SyncDirection.PUSH, "sessions/a.jsonl", 1, 2))
        on_progress(SyncProgress(SyncDirection.PUSH, "sessions/b.jsonl", 2, 2))
        return SyncReport(
            downloaded=["memory/a.md"], uploaded=["sessions/a.jsonl", "sessions/b.jsonl"]
        )

    monkeypatch.setattr("surfaces.cli.commands.remote_sync.run_remote_sync", _fake_run_remote_sync)
    result = runner.invoke(remote_sync_command, ["sync"])
    assert result.exit_code == 0, result.output
    assert "Pulling" in result.output
    assert "Pushing" in result.output
    assert "1 downloaded" in result.output
    assert "2 uploaded" in result.output


def test_sync_stays_script_clean_off_a_tty(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CliRunner's stdout is not a TTY by default: on_progress must stay None."""
    seen: dict[str, object] = {}

    def _capture(
        *,
        pull_only: bool = False,
        push_only: bool = False,
        dry_run: bool = False,
        on_progress: Callable[[SyncProgress], None] | None = None,
    ) -> SyncReport:
        seen["on_progress"] = on_progress
        return SyncReport(uploaded=["sessions/a.jsonl"])

    monkeypatch.setattr("surfaces.cli.commands.remote_sync.run_remote_sync", _capture)
    result = runner.invoke(remote_sync_command, ["sync"])
    assert result.exit_code == 0
    assert seen["on_progress"] is None


def test_sync_passes_direction_flags(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def _capture(
        *,
        pull_only: bool = False,
        push_only: bool = False,
        dry_run: bool = False,
        on_progress: Callable[[SyncProgress], None] | None = None,
    ) -> SyncReport:
        del on_progress
        seen["pull_only"] = pull_only
        seen["push_only"] = push_only
        seen["dry_run"] = dry_run
        return SyncReport()

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        _capture,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--pull-only"])
    assert result.exit_code == 0
    assert seen == {"pull_only": True, "push_only": False, "dry_run": False}


def test_sync_passes_dry_run_and_labels_the_report(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, bool] = {}

    def _capture(
        *,
        pull_only: bool = False,
        push_only: bool = False,
        dry_run: bool = False,
        on_progress: Callable[[SyncProgress], None] | None = None,
    ) -> SyncReport:
        del on_progress
        seen["dry_run"] = dry_run
        return SyncReport(uploaded=["sessions/a.jsonl"])

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        _capture,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--dry-run"])
    assert result.exit_code == 0
    assert seen == {"dry_run": True}
    assert "Dry run" in result.output
    assert "would be uploaded" in result.output


def test_sync_failure_exits_nonzero(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: object) -> SyncReport:
        raise RemoteSyncConfigError("choose one of --pull-only or --push-only, not both")

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        _boom,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--pull-only", "--push-only"])
    assert result.exit_code != 0
    assert "Sync failed" in result.output


def test_default_invocation_is_status(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    result = runner.invoke(remote_sync_command, [])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_cli_group_registered_on_main() -> None:
    from surfaces.cli.app import cli

    ctx = click.Context(cli)
    assert "remote-sync" in cli.list_commands(ctx)


def test_top_level_opensre_remote_sync_help(runner: CliRunner) -> None:
    from surfaces.cli.app import cli

    result = runner.invoke(cli, ["remote-sync", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output
    assert "sync" in result.output


def test_status_error_exits_nonzero(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> SyncStatus:
        raise RemoteSyncConfigError("OPENSRE_REMOTE_SYNC is on but no bucket")

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.get_sync_status",
        _boom,
    )
    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code != 0
    assert "no bucket" in result.output


def test_sync_prints_kept_remote_hint(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    report = SyncReport(
        uploaded=[],
        downloaded=[],
        kept_remote=["sessions/newer.jsonl"],
        skipped=0,
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: report,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--push-only"])
    assert result.exit_code == 0
    assert "sessions/newer.jsonl" in result.output
    assert "full sync" in result.output.lower() or "no --push-only" in result.output


def test_sync_help_documents_direction_flags(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["sync", "--help"])
    assert result.exit_code == 0
    assert "--pull-only" in result.output
    assert "--push-only" in result.output
    assert "--dry-run" in result.output


def test_setup_rejects_flag_provider_does_not_support(runner: CliRunner) -> None:
    result = runner.invoke(
        remote_sync_command,
        ["setup", "--provider", "vercel", "--bucket", "b", "--profile", "dev"],
    )
    assert result.exit_code != 0
    assert "--profile is not used by provider 'vercel'" in result.output


def test_setup_writes_settings_with_supported_flags(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    result = runner.invoke(
        remote_sync_command,
        [
            "setup",
            "--provider",
            "aws",
            "--bucket",
            "b",
            "--region",
            "us-east-1",
            "--profile",
            "dev",
        ],
    )
    assert result.exit_code == 0


def _unexpected_prompt(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("External setup commands must only accept flags")


def _set_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the command observe a TTY, including sync progress rendering."""
    import click

    monkeypatch.setattr(
        click, "get_text_stream", lambda _name: SimpleNamespace(isatty=lambda: True)
    )


def test_setup_missing_bucket_on_tty_requires_flags_without_prompting(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    _set_interactive(monkeypatch)
    monkeypatch.setattr(click, "prompt", _unexpected_prompt)

    # A terminal does not change the external command into an input form.
    result = runner.invoke(remote_sync_command, ["setup"])
    assert result.exit_code != 0
    assert "--bucket" in result.output
    assert not isinstance(result.exception, AssertionError)
    assert not (tmp_path / "config.yml").exists()


def test_setup_partial_provider_flags_never_prompt_or_save(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    _set_interactive(monkeypatch)
    monkeypatch.setattr(click, "prompt", _unexpected_prompt)

    # Optional provider flags cannot replace the required destination.
    # No interactive wizard should collect it for an external command.
    result = runner.invoke(
        remote_sync_command,
        ["setup", "--provider", "aws", "--region", "us-east-1", "--profile", "my-profile"],
    )
    assert result.exit_code != 0
    assert "--bucket" in result.output
    assert not isinstance(result.exception, AssertionError)
    assert not (tmp_path / "config.yml").exists()


def _save_config(
    saved: dict[str, RemoteSyncSetupRequest],
) -> Callable[[RemoteSyncSetupRequest], RemoteSyncConfig]:
    def _save(request: RemoteSyncSetupRequest) -> RemoteSyncConfig:
        saved["request"] = request
        return RemoteSyncConfig(
            bucket=request.bucket, provider=request.provider, prefix=request.prefix
        )

    return _save


def test_setup_with_flags_saves_and_confirms(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[str, RemoteSyncSetupRequest] = {}
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.save_remote_sync_settings", _save_config(saved)
    )
    result = runner.invoke(
        remote_sync_command, ["setup", "--provider", "gcs", "--bucket", "my-bucket"]
    )
    assert result.exit_code == 0
    request = saved["request"]
    assert request.provider == "gcs"
    assert request.bucket == "my-bucket"
    assert "Remote sync settings saved → gcs / my-bucket/opensre" in result.output
    assert "Stored in ~/.opensre/config.yml" in result.output
    assert "gcloud auth application-default login" in result.output
    assert "opensre remote-sync status" in result.output


def test_setup_without_bucket_off_tty_fails(runner: CliRunner) -> None:
    # CliRunner stdin is not a TTY, and external setup still requires flags.
    result = runner.invoke(remote_sync_command, ["setup", "--provider", "gcs"])
    assert result.exit_code != 0
    assert "--bucket" in result.output


def test_setup_unknown_provider_fails_with_known_list(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["setup", "--provider", "gogle", "--bucket", "b"])
    assert result.exit_code != 0
    assert "unknown remote-sync provider" in result.output
    assert "gcs" in result.output


def test_setup_complete_flags_use_default_provider_without_prompting(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    saved: dict[str, RemoteSyncSetupRequest] = {}
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.save_remote_sync_settings", _save_config(saved)
    )
    _set_interactive(monkeypatch)
    monkeypatch.setattr(click, "prompt", _unexpected_prompt)
    # Provider is optional on both surfaces; a supplied destination is complete.
    result = runner.invoke(remote_sync_command, ["setup", "--bucket", "my-bucket"])
    assert result.exit_code == 0
    request = saved["request"]
    assert request.provider == "aws"
    assert request.bucket == "my-bucket"
    assert request.prefix == "opensre"


def test_setup_disabled_without_bucket_switches_off_without_setup_values(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, bool] = {}
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.disable_remote_sync",
        lambda: calls.setdefault("disabled", True),
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.save_remote_sync_settings",
        lambda _request: calls.setdefault("saved", True),
    )
    result = runner.invoke(remote_sync_command, ["setup", "--disabled"])
    assert result.exit_code == 0
    assert calls == {"disabled": True}
    assert "Remote sync is off." in result.output


def test_setup_disabled_with_explicit_flags_is_rejected_not_dropped(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, bool] = {}
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.disable_remote_sync",
        lambda: calls.setdefault("disabled", True),
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.save_remote_sync_settings",
        lambda _request: calls.setdefault("saved", True),
    )
    result = runner.invoke(remote_sync_command, ["setup", "--disabled", "--provider", "vercel"])
    assert result.exit_code != 0
    assert calls == {}
    assert "cannot also set --provider" in result.output
    assert "Pass --bucket" in result.output


def test_setup_disabled_with_bucket_saves_enabled_false(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[str, RemoteSyncSetupRequest] = {}
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.save_remote_sync_settings", _save_config(saved)
    )
    result = runner.invoke(
        remote_sync_command, ["setup", "--provider", "gcs", "--bucket", "b", "--disabled"]
    )
    assert result.exit_code == 0
    assert saved["request"].enabled is False
    # Saying it is off must not come with a "now sync" suggestion.
    assert "Remote sync is off" in result.output
    assert "remote-sync sync" not in result.output


def test_setup_help_lists_the_flags(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["setup", "--help"])
    assert result.exit_code == 0
    assert "--provider" in result.output
    assert "--bucket" in result.output


def test_setup_incomplete_flags_leave_existing_settings_unchanged(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths
    from config.local_settings import update_section

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    update_section("remote_sync", {"bucket": "stored", "provider": "gcs", "enabled": False})
    before = (tmp_path / "config.yml").read_bytes()
    _set_interactive(monkeypatch)
    monkeypatch.setattr(click, "prompt", _unexpected_prompt)
    result = runner.invoke(remote_sync_command, ["setup"])
    assert result.exit_code != 0
    assert "saved" not in result.output
    assert (tmp_path / "config.yml").read_bytes() == before


@pytest.mark.parametrize(
    "flags",
    [
        ["--provider", "vercel", "--bucket", "--prefix", "team/"],
        ["--provider", "aws", "--bucket", "real-bucket", "--region", "--profile", "prod"],
        ["--bucket", "--disabled"],
    ],
)
def test_setup_malformed_flags_never_open_form_or_write(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flags: list[str]
) -> None:
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)

    def unexpected(_app: object) -> None:
        raise AssertionError("Malformed flags must be rejected before opening a form")

    monkeypatch.setattr(click, "prompt", unexpected)
    result = runner.invoke(remote_sync_command, ["setup", *flags])
    assert result.exit_code != 0
    assert not isinstance(result.exception, AssertionError)
    assert not (tmp_path / "config.yml").exists()
