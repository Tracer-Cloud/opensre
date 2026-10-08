"""Global ``--json`` coverage for read-only CLI status and list commands."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY

import pytest
from click.testing import CliRunner

from config.constants.work_items import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.work_items import add_work_item
from infrastructure.filestorage.operations import SyncStatus
from infrastructure.process.runtime_flags import reset_runtime_flags
from tools.system.fleet_monitoring.registry import AgentRecord


@pytest.fixture(autouse=True)
def _reset_runtime_flags() -> None:
    reset_runtime_flags()
    yield
    reset_runtime_flags()


def test_global_json_serializes_fleet_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    monkeypatch.setattr(
        "surfaces.cli.commands.agent.registered_and_discovered_agents",
        lambda _registry: [AgentRecord(name="codex", pid=42, command="codex")],
    )

    result = CliRunner().invoke(cli, ["--json", "fleet", "list"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "name": "codex",
            "pid": 42,
            "command": "codex",
            "source": "registered",
            "waits_on": [],
            "provider": None,
            "registered_at": ANY,
        }
    ]


def test_global_json_serializes_work_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work_items"))
    item = add_work_item(title="check latency")

    result = CliRunner().invoke(cli, ["--json", "work", "list"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [item.to_dict()]


def test_global_json_serializes_empty_cron_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    monkeypatch.setattr("infrastructure.scheduling.scheduler.loops.list_loop_summaries", lambda: [])

    result = CliRunner().invoke(cli, ["--json", "cron", "list"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_global_json_serializes_gateway_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    monkeypatch.setattr("surfaces.cli.commands.gateway.gateway_daemon_pid", lambda: 123)
    monkeypatch.setattr(
        "surfaces.cli.commands.gateway.read_component_status",
        lambda: {"scheduler": "running", "web": "stopped"},
    )

    result = CliRunner().invoke(cli, ["--json", "gateway", "status"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "running": True,
        "pid": 123,
        "components": {"scheduler": "running", "web": "stopped"},
        "log_file": ANY,
    }


def test_global_json_serializes_remote_sync_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.get_sync_status",
        lambda: SyncStatus(config=None, roots=()),
    )

    result = CliRunner().invoke(cli, ["--json", "remote-sync", "status"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "enabled": False,
        "config": None,
        "roots": [],
        "exposure": None,
    }


def test_global_json_serializes_skills_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    snapshot = SimpleNamespace(release="builtin", source="package", skills=(), diagnostics=())
    monkeypatch.setattr(
        "surfaces.cli.commands.skills.active_skill_catalog",
        lambda: SimpleNamespace(current=lambda: snapshot),
    )
    monkeypatch.setattr("surfaces.cli.commands.skills.read_state", lambda: {})
    monkeypatch.setattr("surfaces.cli.commands.skills.latest_stored_seq", lambda: None)
    monkeypatch.setattr(
        "surfaces.cli.commands.skills.fetch_release",
        lambda _app_url: SimpleNamespace(release=None),
    )

    result = CliRunner().invoke(cli, ["--json", "skills", "status"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "active": {"release": "builtin", "source": "package", "skills_count": 0},
        "auto_update": False,
        "cached": None,
        "last_check_seconds_ago": None,
        "rejected_seq": None,
        "diagnostics": [],
        "published": None,
    }


def test_global_json_serializes_empty_runbooks_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import local_settings
    from surfaces.cli.app import cli

    monkeypatch.setattr(local_settings.paths, "OPENSRE_HOME_DIR", tmp_path)

    result = CliRunner().invoke(cli, ["--json", "runbooks", "list"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_global_json_serializes_empty_guardrails_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from surfaces.cli.app import cli

    monkeypatch.setattr(
        "infrastructure.safety.guardrails.rules.get_default_rules_path",
        lambda: tmp_path / "missing-guardrails.yml",
    )

    result = CliRunner().invoke(cli, ["--json", "guardrails", "rules"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []
