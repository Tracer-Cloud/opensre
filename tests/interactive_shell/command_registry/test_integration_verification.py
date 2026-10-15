"""Verification children cannot outlive browser cancellation or their output budget."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from surfaces.interactive_shell.command_registry import integration_verification as verification


@pytest.mark.asyncio
async def test_failed_connectivity_is_a_valid_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"service": "grafana", "status": "failed", "detail": "ConnectionError"}
    program = f"print({json.dumps([expected])!r}); raise SystemExit(1)"
    monkeypatch.setattr(
        verification, "opensre_command", lambda *_args: [sys.executable, "-c", program]
    )
    assert await verification.verify_connection("grafana") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_stalled_verification_is_killed_and_reaped(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    original = asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []

    async def launch(*_args: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await original(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(verification.asyncio, "create_subprocess_exec", launch)
    if not cancel:
        monkeypatch.setattr(verification, "_VERIFY_TIMEOUT_SECONDS", 0.05)
    task = asyncio.create_task(verification.verify_connection("github"))
    await asyncio.wait_for(started.wait(), timeout=3)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await asyncio.wait_for(task, timeout=3)
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_excess_output_is_bounded_and_the_worker_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verification, "_MAX_RESULT_BYTES", 32)
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
        await asyncio.wait_for(verification.verify_connection("github"), timeout=3)
