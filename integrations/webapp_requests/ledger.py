"""The outcome of every web app request this gateway applied, kept on disk.

The web app redelivers a request until it is acknowledged (the Tasks page's
loop changes, the Knowledge page's forgets and rescans). A redelivery must
re-send the first outcome, never apply the change again: re-applying a pause
after a member resumed the loop in Slack, or a forget after the member said
the fact again, undoes their later change. Each feature keeps its own ledger
file on the durable volume; entries are forgotten after the web app has long
stopped redelivering them.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from filelock import FileLock

logger = logging.getLogger(__name__)

#: Far past the web app's redelivery: it settles a request on its first ack.
_RETENTION_SECONDS = 30 * 24 * 60 * 60
_LOCK_TIMEOUT_SECONDS = 10.0


class LedgerUnavailable(RuntimeError):
    """The ledger could not be read or written; the request must wait."""


@dataclass(frozen=True, slots=True)
class RecordedOutcome:
    """What applying one request did, as first acknowledged.

    ``ref`` names what the request produced (a loop id), when anything.
    """

    applied: bool
    ref: str = ""
    error: str = ""


class RequestLedger:
    """Request id -> outcome, per organization, guarded by a file lock."""

    def __init__(
        self,
        path: Path,
        *,
        log_tag: str = "webapp-requests",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._log_tag = log_tag
        self._clock = clock

    def get(self, organization: str, request_id: str) -> RecordedOutcome | None:
        """The recorded outcome, or ``None`` for a request never applied here."""
        with self._locked():
            entry = self._read().get(_key(organization, request_id))
        if not isinstance(entry, dict):
            return None
        return RecordedOutcome(
            applied=bool(entry.get("applied")),
            # Ledgers written before the ledger was shared call it loop_id.
            ref=str(entry.get("ref") or entry.get("loop_id") or ""),
            error=str(entry.get("error") or ""),
        )

    def record(self, organization: str, request_id: str, outcome: RecordedOutcome) -> None:
        """Store ``outcome`` and drop entries past retention."""
        now = self._clock()
        with self._locked():
            entries = {
                key: entry
                for key, entry in self._read().items()
                if isinstance(entry, dict)
                and now - float(entry.get("recorded_at") or 0) < _RETENTION_SECONDS
            }
            entries[_key(organization, request_id)] = {**asdict(outcome), "recorded_at": now}
            self._write(entries)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self._path) + ".lock", timeout=_LOCK_TIMEOUT_SECONDS):
                yield
        except (OSError, TimeoutError) as exc:
            raise LedgerUnavailable(type(exc).__name__) from exc

    def _read(self) -> dict[str, object]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Only a write interrupted outside the atomic rename leaves this;
            # keep it for inspection and start over rather than block every request.
            aside = self._path.with_name(f"{self._path.name}.corrupt-{int(self._clock())}")
            os.replace(self._path, aside)
            logger.error("[%s] request ledger was unreadable; moved to %s", self._log_tag, aside)
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, entries: Mapping[str, object]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=f".{self._path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(entries, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def _key(organization: str, request_id: str) -> str:
    return f"{organization}:{request_id.lower()}"


__all__ = ["LedgerUnavailable", "RecordedOutcome", "RequestLedger"]
