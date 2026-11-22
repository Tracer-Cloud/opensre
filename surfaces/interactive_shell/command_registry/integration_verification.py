"""Bound connectivity checks to a child process owned by the connection browser."""

from __future__ import annotations

import asyncio
import json
import os
from threading import Event
from typing import cast

from config.constants.analytics import ANALYTICS_DISABLED_ENV
from config.interactive_override import interactive_override_env
from config.scope_handoff import hand_off_scope
from infrastructure.process.entrypoint import opensre_command
from tools.interactive_shell.shell.execution import execute_process_command

_VERIFY_TIMEOUT_SECONDS = 30
_MAX_RESULT_BYTES = 64 * 1024


def _combined(rows: list[dict[str, str]]) -> dict[str, str]:
    """One row per service: a per-instance service passes only if every instance does."""
    if len(rows) == 1:
        return rows[0]
    failing = [row for row in rows if row["status"] != "passed"]
    if not failing:
        return {**rows[0], "detail": f"All {len(rows)} instances passed."}
    detail = "; ".join(
        f"{row.get('instance') or '-'}: {row.get('detail') or row['status']}" for row in failing
    )
    return {**failing[0], "detail": detail}


async def verify_connection(service: str) -> dict[str, str]:
    """Read one CLI verification result; kill and reap the worker on timeout or cancel."""
    environment = dict(os.environ)
    hand_off_scope(environment)
    environment.update(interactive_override_env())
    # This is an internal probe, not another user CLI invocation to snapshot/flush.
    environment[ANALYTICS_DISABLED_ENV] = "1"
    cancel = Event()
    worker = asyncio.create_task(
        asyncio.to_thread(
            execute_process_command,
            argv=opensre_command("--json", "integrations", "verify", service),
            timeout_seconds=_VERIFY_TIMEOUT_SECONDS,
            max_output_bytes=_MAX_RESULT_BYTES,
            cancel_event=cancel,
            environment=environment,
        )
    )
    try:
        result = await asyncio.shield(worker)
        if result.truncated:
            raise ValueError("Verification response exceeds the display budget")
        if result.timed_out:
            raise TimeoutError("Connectivity check timed out")
        rows = json.loads(result.stdout)
        if result.exit_code not in (0, 1) or not isinstance(rows, list) or not rows:
            raise ValueError("Invalid verification response")
        for row in rows:
            if (
                not isinstance(row, dict)
                or not {"service", "status"} <= row.keys()
                or not all(
                    isinstance(key, str) and isinstance(value, str) for key, value in row.items()
                )
            ):
                raise ValueError("Invalid verification row")
        return _combined(cast(list[dict[str, str]], rows))
    finally:
        cancel.set()
        await asyncio.gather(worker)
