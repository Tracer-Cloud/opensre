"""A task a hosted turn creates through a CLI child names that turn's member and organization.

The child process sees the turn only through the environment its parent built.
Each case runs the real command on exactly that environment and outside the
turn's context, as a child process would.
"""

from __future__ import annotations

import contextlib
import contextvars
import io
import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.tenancy import TURN_ACTOR_ID_ENV, TURN_ORGANIZATION_ID_ENV
from config.constants.work_items import OPENSRE_WORK_ITEMS_DIR_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from infrastructure.scheduling.scheduler.loop_constants import LOOP_CREATED_BY_PARAM
from infrastructure.scheduling.scheduler.storage import task_store
from surfaces.cli.commands.cron import cron_command
from surfaces.cli.commands.work import work_command
from surfaces.interactive_shell.command_registry import cli_parity
from tools.interactive_shell.cli import run_opensre_cli_command_result
from tools.interactive_shell.subprocess_presenter import HeadlessSubprocessPresenter

_ALICE_IN_ORG_A = StorageScope(principal=Principal.org("org_A"), actor=Actor(id="U_ALICE"))
_COMMAND_GROUPS: dict[str, click.Group] = {"cron": cron_command, "work": work_command}
_CRON_ADD = [
    "cron", "add", "--kind", "manual_loop", "--cron", "0 9 * * *",
    "--provider", "interactive_shell", "--prompt", "Check incidents.",
]  # fmt: skip
_WORK_REMINDER = [
    "work", "add", "Ship the release", "--remind-at", "2030-01-01T09:00:00+00:00",
    "--provider", "slack", "--chat-id", "C123",
]  # fmt: skip


def _run_cli_child(
    cmd: list[str], *, env: dict[str, str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    """Stand in for the child process: the CLI sees ``env`` and none of the turn's context."""
    child_env = {key: env.get(key) for key in {*os.environ, *env}}
    start = next(index for index, part in enumerate(cmd) if part in _COMMAND_GROUPS)
    result = contextvars.Context().run(
        CliRunner().invoke, _COMMAND_GROUPS[cmd[start]], cmd[start + 1 :], env=child_env
    )
    return subprocess.CompletedProcess(cmd, result.exit_code, stdout=result.output, stderr="")


def _slash_command(args: list[str]) -> bool:
    """``/cron add …`` typed into the turn: the shell's CLI-parity child."""
    session = SessionCore(store=InMemorySessionStore())
    return cli_parity.run_cli_command(Console(file=io.StringIO()), args, session=session)


def _cli_exec(args: list[str]) -> bool:
    """The agent's ``cli_exec`` tool running ``opensre …`` in the foreground."""
    presenter = HeadlessSubprocessPresenter(SessionCore(store=InMemorySessionStore()))
    result = run_opensre_cli_command_result(shlex.join(args), presenter, prefer_foreground=True)
    return result.foreground is not None and result.foreground.exit_code == 0


@pytest.mark.parametrize(
    ("launch", "args"),
    [(_slash_command, _CRON_ADD), (_cli_exec, _CRON_ADD), (_cli_exec, _WORK_REMINDER)],
    ids=["slash-cron-add", "cli-exec-cron-add", "cli-exec-work-reminder"],
)
@pytest.mark.parametrize(
    ("scope", "created_by", "organization"),
    [(_ALICE_IN_ORG_A, "U_ALICE", "org_A"), (None, None, "")],
    ids=["hosted-turn", "no-turn"],
)
def test_a_task_created_by_a_turns_cli_child_names_that_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launch: Callable[[list[str]], bool],
    args: list[str],
    scope: StorageScope | None,
    created_by: str | None,
    organization: str,
) -> None:
    # Arrange
    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work_items"))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_A")
    # A claim already in the gateway's own environment must never reach the child.
    monkeypatch.setenv(TURN_ORGANIZATION_ID_ENV, "org_A")
    monkeypatch.setenv(TURN_ACTOR_ID_ENV, "U_MALLORY")
    monkeypatch.setattr(subprocess, "run", _run_cli_child)

    # Act
    with bound_storage_scope(scope) if scope is not None else contextlib.nullcontext():
        launched = launch(args)

    # Assert
    assert launched is True
    (task,) = task_store.list_tasks(store)
    assert task.params.get(LOOP_CREATED_BY_PARAM) == created_by
    assert task.organization == organization
