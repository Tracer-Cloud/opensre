"""The packaged worker command reaches the same deadline-guarded implementation."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from config.constants.ci_repair import CI_REPAIR_WORKER_COMMAND
from infrastructure.process import entrypoint
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.storage import RepairStore
from surfaces.cli.app import cli


def test_frozen_child_command_is_parseable_and_obeys_the_saved_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RepairStore(tmp_path)
    run = RepairRun(
        id="a" * 12,
        owner="alice",
        actor="alice",
        repo="demo",
        demo=True,
        started_at=time.time() - 601,
        deadline=time.time() - 1,
    )
    store.save(run)
    monkeypatch.setattr(entrypoint.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entrypoint.sys, "executable", "/Applications/opensre")
    command = entrypoint.opensre_command(CI_REPAIR_WORKER_COMMAND, str(tmp_path), run.id)
    assert command[:2] == ["/Applications/opensre", CI_REPAIR_WORKER_COMMAND]
    result = CliRunner().invoke(cli, command[1:])
    assert result.exit_code == 0, result.output
    assert store.get(run.id).status is RepairStatus.TIMED_OUT
    assert entrypoint.opensre_command("cron", "start", "--service") == [
        "/Applications/opensre",
        "cron",
        "start",
        "--service",
    ]
