"""Global ``--json`` coverage for read-only CLI status and list commands."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY

import pytest
from click.testing import CliRunner

from config.constants.work_items import OPENSRE_WORK_ITEMS_DIR_ENV
from config.runbook_sources import RunbookSourceConfig
from core.domain.work_items import add_work_item
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.enums import BucketExposure, SyncRootName
from infrastructure.filestorage.exclusions import ExclusionRules
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.operations import SyncRootStatus, SyncStatus
from infrastructure.process.runtime_flags import reset_runtime_flags
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.types import Provider, TaskKind
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


def test_global_json_serializes_cron_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    loop = LoopSummary(
        id="loop-123",
        task_ids=("task-a", "task-b"),
        name="Morning health",
        description="Check critical services",
        prompt="",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * *",
        timezone="UTC",
        provider=Provider.SLACK,
        chat_id="C123",
        channels=("slack:C123",),
        enabled=True,
        window_hours=24,
        last_run="2026-10-08T09:00:00+00:00",
        next_run="2026-10-09T09:00:00+00:00",
        schedule_error="",
        mode="report",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.loops.list_loop_summaries", lambda: [loop]
    )

    result = CliRunner().invoke(cli, ["--json", "cron", "list"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "id": "loop-123",
            "task_ids": ["task-a", "task-b"],
            "name": "Morning health",
            "description": "Check critical services",
            "kind": "manual_loop",
            "cron": "0 9 * * *",
            "timezone": "UTC",
            "provider": "slack",
            "chat_id": "C123",
            "channels": ["slack:C123"],
            "enabled": True,
            "status": "active",
            "last_run": "2026-10-08T09:00:00+00:00",
            "next_run": "2026-10-09T09:00:00+00:00",
            "schedule_error": "",
            "mode": "report",
        }
    ]


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

    status = SyncStatus(
        config=RemoteSyncConfig(
            bucket="incident-data",
            provider="aws",
            prefix="opensre",
            region="eu-west-1",
            profile="oncall",
            exclude=ExclusionRules(("sessions/private/*",)),
        ),
        roots=(
            SyncRootStatus(SyncRootName.SESSIONS, Path("/tmp/sessions"), True, excluded=2),
            SyncRootStatus(SyncRootName.MEMORY, Path("/tmp/memory"), False),
        ),
        exposure=PublicAccessStatus(BucketExposure.PRIVATE),
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.get_sync_status",
        lambda: status,
    )

    result = CliRunner().invoke(cli, ["--json", "remote-sync", "status"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "enabled": True,
        "config": {
            "provider": "aws",
            "bucket": "incident-data",
            "prefix": "opensre",
            "region": "eu-west-1",
            "profile": "oncall",
            "exclusions": ["sessions/private/*"],
        },
        "roots": [
            {"name": "sessions", "path": "/tmp/sessions", "exists": True, "excluded": 2},
            {"name": "memory", "path": "/tmp/memory", "exists": False, "excluded": 0},
        ],
        "exposure": {"status": "private", "detail": ""},
    }


def test_global_json_serializes_skills_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    snapshot = SimpleNamespace(release="builtin", source="package", skills=(), diagnostics=())

    def _current_snapshot() -> SimpleNamespace:
        return snapshot

    def _active_skill_catalog() -> SimpleNamespace:
        return SimpleNamespace(current=_current_snapshot)

    monkeypatch.setattr(
        "surfaces.cli.commands.skills.active_skill_catalog",
        _active_skill_catalog,
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


def test_global_json_serializes_runbooks_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.cli.app import cli

    source = RunbookSourceConfig(
        name="platform",
        provider="github",
        repository="acme/runbooks",
        ref="stable",
        manifest="runbooks.yml",
    )
    monkeypatch.setattr("surfaces.cli.commands.runbooks.load_runbook_sources", lambda: (source,))

    result = CliRunner().invoke(cli, ["--json", "runbooks", "list"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "name": "platform",
            "provider": "github",
            "repository": "acme/runbooks",
            "ref": "stable",
            "manifest": "runbooks.yml",
        }
    ]


def test_global_json_serializes_guardrails_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from surfaces.cli.app import cli

    rules_path = tmp_path / "guardrails.yml"
    rules_path.write_text(
        """rules:
  - name: deploy_token
    action: redact
    patterns:
      - token-[0-9]+
    keywords:
      - deploy
    description: Deployment token
    replacement: "[REDACTED]"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.rules.get_default_rules_path",
        lambda: rules_path,
    )

    result = CliRunner().invoke(cli, ["--json", "guardrails", "rules"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "name": "deploy_token",
            "action": "redact",
            "patterns": ["token-[0-9]+"],
            "keywords": ["deploy"],
            "description": "Deployment token",
            "replacement": "[REDACTED]",
            "enabled": True,
        }
    ]
