"""Cross-surface contract: CLI, REPL, and gateway share one service seam.

Fails when a surface stops calling ``get_sync_status`` / ``run_remote_sync``
or when the command name diverges across adapters.
"""

from __future__ import annotations

import ast
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from surfaces.cli.app import cli
from surfaces.gateway_entry import gateway_slash_ports_factory
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.help import _help_sections
from tools.interactive_shell.shared.slash_catalog import MCP_BY_COMMAND

_REPO = Path(__file__).resolve().parents[2]
_CLI_MOD = _REPO / "surfaces/cli/commands/remote_sync.py"
_SLASH_MOD = _REPO / "surfaces/interactive_shell/command_registry/remote_sync_cmds.py"


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and "filestorage.operations" in node.module
        ):
            for alias in node.names:
                names.add(alias.name)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            names.add(node.attr)
    return names


def test_cli_and_slash_modules_import_shared_service_api() -> None:
    cli_names = _imported_names(_CLI_MOD)
    slash_names = _imported_names(_SLASH_MOD)
    for required in ("get_sync_status", "run_remote_sync"):
        assert required in cli_names, f"CLI adapter must use {required}"
        assert required in slash_names, f"slash adapter must use {required}"


def test_command_names_align_across_surfaces() -> None:
    ctx = click.Context(cli)
    assert "remote-sync" in cli.list_commands(ctx)
    assert "/remote-sync" in SLASH_COMMANDS
    assert "/remote-sync" in MCP_BY_COMMAND
    assert gateway_slash_ports_factory().command_exists("/remote-sync")


def test_help_lists_remote_sync_for_repl() -> None:
    sections = dict(_help_sections())
    assert "Remote sync" in sections
    names = {cmd.name for cmd in sections["Remote sync"]}
    assert "/remote-sync" in names


def test_top_level_cli_remote_sync_status_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: registration in commands/__init__ + LazyRichGroup."""
    from config.constants import paths as paths_mod
    from config.constants.filestorage import REMOTE_SYNC_BUCKET_ENV, REMOTE_SYNC_ENV

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)

    result = CliRunner().invoke(cli, ["remote-sync", "status"])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_top_level_cli_remote_sync_sync_uses_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from infrastructure.filestorage.engine import SyncReport

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: SyncReport(uploaded=["sessions/x.jsonl"], skipped=2),
    )
    result = CliRunner().invoke(cli, ["remote-sync", "sync", "--push-only"])
    assert result.exit_code == 0
    assert "1 uploaded" in result.output
    assert "2 already current" in result.output


def test_top_level_cli_remote_sync_setup_writes_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "remote-sync",
            "setup",
            "--provider",
            "vercel",
            "--bucket",
            "opensre-remote-sync",
            "--prefix",
            "opensre",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "settings saved" in result.output
    assert "vercel" in result.output
    assert "BLOB_READ_WRITE_TOKEN" in result.output
    assert (tmp_path / "config.yml").is_file()
