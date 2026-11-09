"""Durable remote-prompt records, so a replacement task still answers what its predecessor took.

One JSON line per change, appended and fsynced; the newest ``revision`` of a
prompt wins on read, so two writers racing on one prompt cannot leave an older
state on top. :meth:`JsonlPromptJobStore.compact` rewrites the file through a
temporary file and an atomic rename with only the newest record of each prompt
it keeps, which also drops a line torn by a killed writer. A file lock
serializes writers, including an outgoing and an incoming task sharing the
mount during a replacement.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from filelock import FileLock, Timeout

from config.constants.gateway import PROMPT_JOBS_FILE, PROMPT_JOBS_LOCK_TIMEOUT_SECONDS
from config.constants.paths import deployment_home

logger = logging.getLogger(__name__)

#: Owner-only: the records hold the organization's prompts and answers.
_FILE_MODE = 0o600


class PromptJobStore(Protocol):
    """Where :class:`~gateway.core.prompt_intake.jobs.PromptQueue` keeps its records."""

    def load(self) -> list[dict[str, Any]]:
        """The newest saved record of every prompt; empty when nothing can be read."""

    def save(self, record: Mapping[str, Any]) -> bool:
        """Persist one prompt's record and say whether it reached the disk; never raises."""

    def compare_and_append(
        self, decide: Callable[[Mapping[str, dict[str, Any]]], Sequence[Mapping[str, Any]]]
    ) -> bool:
        """Append what ``decide`` returns for the newest record of every prompt, atomically.

        No other writer, in this task or another, writes between the read and the
        append. ``decide`` gets the records by prompt id and may raise to write
        nothing; its exception propagates. False when the store could not be read
        or written.
        """

    def compact(self, *, drop: Collection[str] = ()) -> None:
        """Keep only the newest record of each prompt, leaving out the prompt ids in ``drop``."""


def prompt_jobs_path() -> Path:
    """The record file on the deployment's home: the organization's mount on a silo."""
    return deployment_home() / PROMPT_JOBS_FILE


class JsonlPromptJobStore:
    """:class:`PromptJobStore` as an append-only JSONL file."""

    def __init__(
        self, path: Path, *, lock_timeout_seconds: float = PROMPT_JOBS_LOCK_TIMEOUT_SECONDS
    ) -> None:
        self._path = path
        self._file_lock = FileLock(
            str(path.with_name(f"{path.name}.lock")), timeout=lock_timeout_seconds
        )
        self._guard = threading.Lock()

    def load(self) -> list[dict[str, Any]]:
        try:
            with self._locked():
                return list(self._read().values())
        except (OSError, Timeout) as exc:
            logger.warning("[gateway] prompt records unreadable: %s", type(exc).__name__)
            return []

    def save(self, record: Mapping[str, Any]) -> bool:
        try:
            data = _lines([record])
            with self._locked():
                self._append(data)
        except (OSError, Timeout, TypeError, ValueError) as exc:
            logger.warning("[gateway] prompt record write failed: %s", type(exc).__name__)
            return False
        return True

    def compare_and_append(
        self, decide: Callable[[Mapping[str, dict[str, Any]]], Sequence[Mapping[str, Any]]]
    ) -> bool:
        try:
            with self._locked():
                data = _lines(decide(self._read()))
                if data:
                    # One write, so the records land together or (torn) not at all.
                    self._append(data)
        except (OSError, Timeout, TypeError, ValueError) as exc:
            logger.warning("[gateway] prompt record claim failed: %s", type(exc).__name__)
            return False
        return True

    def compact(self, *, drop: Collection[str] = ()) -> None:
        try:
            with self._locked():
                if not self._path.is_file():
                    return
                dropped = set(drop)
                kept = [r for job_id, r in self._read().items() if job_id not in dropped]
                self._replace(kept)
        except (OSError, Timeout, TypeError, ValueError) as exc:
            # The file is left as it was: an unreadable file must not be replaced by an empty one.
            logger.warning("[gateway] prompt record compaction failed: %s", type(exc).__name__)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        with self._guard:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._file_lock:
                yield

    def _read(self) -> dict[str, dict[str, Any]]:
        """Newest record per prompt id; a line that does not parse is skipped. Raises ``OSError``."""
        if not self._path.is_file():
            return {}
        newest: dict[str, dict[str, Any]] = {}
        with self._path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                record = _parsed(line)
                if record is None:
                    continue
                job_id = record["id"]
                held = newest.get(job_id)
                if held is None or record["revision"] >= held["revision"]:
                    newest[job_id] = record
        return newest

    def _append(self, data: bytes) -> None:
        descriptor = os.open(self._path, os.O_RDWR | os.O_APPEND | os.O_CREAT, _FILE_MODE)
        with os.fdopen(descriptor, "r+b") as handle:
            end = handle.seek(0, os.SEEK_END)
            if end:
                handle.seek(end - 1)
                if handle.read(1) != b"\n":
                    # A writer died mid-line; start a fresh line so this record still parses.
                    data = b"\n" + data
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def _replace(self, records: list[dict[str, Any]]) -> None:
        descriptor, temporary = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}."
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def _lines(records: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        for record in records
    )


def _parsed(line: str) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    job_id = record.get("id")
    revision = record.get("revision")
    if not isinstance(job_id, str) or not job_id:
        return None
    if not isinstance(revision, int) or isinstance(revision, bool):
        return None
    return record


__all__ = ["JsonlPromptJobStore", "PromptJobStore", "prompt_jobs_path"]
