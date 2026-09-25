"""Persist an immutable installation observation before its first delivery attempt."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import uuid
from pathlib import Path


def _read(path: Path, identity: str) -> bytes:
    body = path.read_bytes()
    payload = json.loads(body)
    if (
        not isinstance(payload, dict)
        or payload.get("anonymous_id") != identity
        or payload.get("event") != "install_detected"
        or payload.get("event_id")
        not in {f"install_detected:{identity}", f"install_detected:{identity}:delivery-v1"}
        or not isinstance(payload.get("properties"), dict)
        or not isinstance(payload.get("occurred_at"), str)
    ):
        raise ValueError("Invalid persisted installation observation")
    return body


def persist_observation(config_dir: Path, identity: str, body: bytes) -> bytes:
    """Return the first fully written observation across concurrent processes.

    Only the sanitized event body is saved; destinations and credentials are not.
    Retain it after acknowledgement so loss of a receipt cannot redate an event.
    A hard-link publishes a complete fsynced file without overwriting a winner or
    leaving a lock behind when a process crashes.
    """
    directory = config_dir / "install-events-v1"
    path = directory / f"{hashlib.sha256(identity.encode()).hexdigest()}.json"
    if path.exists():
        return _read(path, identity)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        with contextlib.suppress(FileExistsError):
            os.link(temporary, path)
        if os.name != "nt":
            with contextlib.suppress(OSError):
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        return _read(path, identity)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()
