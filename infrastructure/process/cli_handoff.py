"""Detached CLI host with bounded foreground capture and continuing pipe drains."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import BinaryIO

_CAPTURE_LIMIT_BYTES = 128 * 1024
_DRAIN_CHUNK_BYTES = 65536


def _relay_foreground(source: BinaryIO, target: BinaryIO, deadline: float | None) -> None:
    """Relay at most the foreground byte budget, then drain and discard until EOF."""
    remaining = _CAPTURE_LIMIT_BYTES
    try:
        while chunk := os.read(source.fileno(), _DRAIN_CHUNK_BYTES):
            if deadline is not None and time.monotonic() >= deadline:
                remaining = 0
            if remaining:
                kept = chunk[:remaining]
                remaining -= len(kept)
                try:
                    target.write(kept)
                    target.flush()
                except OSError:
                    # Even an unavailable capture destination must not stop
                    # draining and stall the scheduled tick on a full pipe.
                    remaining = 0
    finally:
        source.close()


def run_cli_handoff(args: list[str]) -> int:
    """Run the encoded argv, retaining bounded output only during its foreground window."""
    if len(args) != 2:
        raise ValueError("CLI handoff requires a command and foreground timeout")
    command = json.loads(args[0])
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(token, str) for token in command)
    ):
        raise ValueError("CLI handoff requires an argv list")
    timeout = None if args[1] == "None" else float(args[1])
    deadline = None if timeout is None else time.monotonic() + timeout
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    readers: list[threading.Thread] = []
    for source, target in (
        (process.stdout, sys.stdout.buffer),
        (process.stderr, sys.stderr.buffer),
    ):
        if source is None:
            continue
        reader = threading.Thread(
            target=_relay_foreground, args=(source, target, deadline), daemon=True
        )
        reader.start()
        readers.append(reader)
    try:
        result = process.wait()
        for reader in readers:
            reader.join()
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


__all__ = ["run_cli_handoff"]
