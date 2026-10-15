"""Verification children cannot outlive browser cancellation or their output budget."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

import psutil
import pytest

from surfaces.interactive_shell.command_registry import integration_verification as verification


async def _started_pid(path: Path) -> int:
    async with asyncio.timeout(3):
        while not path.exists() or not path.read_text():
            await asyncio.sleep(0.01)
        return int(path.read_text())


def _assert_stopped(pid: int) -> None:
    try:
        assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return


@pytest.mark.asyncio
async def test_failed_connectivity_is_a_valid_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"service": "grafana", "status": "failed", "detail": "ConnectionError"}
    program = f"import sys; assert sys.stdin.read() == ''; print({json.dumps([expected])!r}); raise SystemExit(1)"
    monkeypatch.setattr(
        verification, "opensre_command", lambda *_args: [sys.executable, "-c", program]
    )
    assert await verification.verify_connection("grafana") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("root_exits", [False, True])
async def test_stalled_verification_tree_is_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cancel: bool, root_exits: bool
) -> None:
    pid_path = tmp_path / "worker.pid"
    payload = f"import os,time; open({str(pid_path)!r},'w').write(str(os.getpid())); time.sleep(60)"
    program = (
        f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{payload!r}])"
        if root_exits
        else payload
    )
    monkeypatch.setattr(
        verification, "opensre_command", lambda *_args: [sys.executable, "-c", program]
    )
    if not cancel:
        monkeypatch.setattr(verification, "_VERIFY_TIMEOUT_SECONDS", 0.5)
    task = asyncio.create_task(verification.verify_connection("github"))
    pid = await _started_pid(pid_path)
    if cancel:
        task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await asyncio.wait_for(task, timeout=4)
        _assert_stopped(pid)
    finally:
        if not task.done():
            task.cancel()
        if psutil.pid_exists(pid):
            with contextlib.suppress(psutil.NoSuchProcess):
                psutil.Process(pid).kill()


@pytest.mark.asyncio
async def test_excess_output_is_bounded_and_the_worker_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verification, "_MAX_RESULT_BYTES", 32)
    monkeypatch.setattr(verification, "_VERIFY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(
        verification,
        "opensre_command",
        lambda *_args: [
            sys.executable,
            "-c",
            "import sys,time; sys.stdout.write('x'*1000000); sys.stdout.flush(); time.sleep(60)",
        ],
    )
    with pytest.raises(ValueError, match="display budget"):
        await asyncio.wait_for(verification.verify_connection("github"), timeout=4)


@pytest.mark.asyncio
async def test_verification_hands_off_scope_without_mutating_parent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.constants.analytics import ANALYTICS_DISABLED_ENV
    from config.constants.product import OPENSRE_INTERACTIVE_ENV
    from config.constants.tenancy import TURN_ACTOR_ID_ENV, TURN_ORGANIZATION_ID_ENV
    from config.interactive_override import forced_non_interactive
    from config.principal import Actor, Principal, StorageScope
    from config.scope_context import bound_storage_scope
    from tools.interactive_shell.shell.execution import ShellExecutionResult

    seen: list[dict[str, str]] = []

    def execute(**kwargs: object) -> ShellExecutionResult:
        environment = kwargs["environment"]
        assert isinstance(environment, dict)
        seen.append(environment)
        return ShellExecutionResult(
            command="verification",
            stdout='[{"service":"github","status":"ok"}]',
            stderr="",
            exit_code=0,
            timed_out=False,
            truncated=False,
            executed_with_shell=False,
        )

    monkeypatch.setattr(verification, "execute_process_command", execute)
    monkeypatch.setenv(ANALYTICS_DISABLED_ENV, "0")
    monkeypatch.setenv(TURN_ORGANIZATION_ID_ENV, "stale-org")
    monkeypatch.setenv(TURN_ACTOR_ID_ENV, "stale-actor")
    scope = StorageScope(principal=Principal.org("current-org"), actor=Actor(id="current-actor"))
    with bound_storage_scope(scope), forced_non_interactive():
        assert (await verification.verify_connection("github"))["status"] == "ok"
    assert seen[0][TURN_ORGANIZATION_ID_ENV] == "current-org"
    assert seen[0][TURN_ACTOR_ID_ENV] == "current-actor"
    assert seen[0][OPENSRE_INTERACTIVE_ENV] == "0"
    assert seen[0][ANALYTICS_DISABLED_ENV] == "1"
    assert os.environ[ANALYTICS_DISABLED_ENV] == "0"
    assert os.environ[TURN_ORGANIZATION_ID_ENV] == "stale-org"
    assert os.environ[TURN_ACTOR_ID_ENV] == "stale-actor"
