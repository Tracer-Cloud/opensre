"""Bound connectivity checks to a child process owned by the connection browser."""

from __future__ import annotations

import asyncio
import json
from typing import cast

from infrastructure.process.entrypoint import opensre_command
from infrastructure.process.termination import terminate_process_tree

_VERIFY_TIMEOUT_SECONDS = 30
_MAX_RESULT_BYTES = 64 * 1024


async def verify_connection(service: str) -> dict[str, str]:
    """Read one CLI verification result; kill and reap the worker on timeout or cancel."""
    process = await asyncio.create_subprocess_exec(
        *opensre_command("--json", "integrations", "verify", service),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert process.stdout is not None
        async with asyncio.timeout(_VERIFY_TIMEOUT_SECONDS):
            output = bytearray()
            while chunk := await process.stdout.read(
                min(8192, _MAX_RESULT_BYTES + 1 - len(output))
            ):
                output.extend(chunk)
                if len(output) > _MAX_RESULT_BYTES:
                    raise ValueError("Verification response exceeds the display budget")
            await process.wait()
        rows = json.loads(output)
        if process.returncode not in (0, 1) or not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("Invalid verification response")
        row = rows[0]
        if (
            not isinstance(row, dict)
            or not {"service", "status"} <= row.keys()
            or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in row.items()
            )
        ):
            raise ValueError("Invalid verification row")
        return cast(dict[str, str], row)
    finally:
        if process.returncode is None:
            terminate_process_tree(process.pid, grace_seconds=0, force_wait_seconds=1)
        await process.communicate()
