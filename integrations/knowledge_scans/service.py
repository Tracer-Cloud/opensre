"""Keep workspace knowledge current: scan each connected integration when it is due.

A scan is due when the integration is connected and any entry it writes is
missing (it was just connected, or someone deleted an entry), older than
:data:`KNOWLEDGE_SCAN_MAX_AGE_SECONDS`, or a rescan was requested; and again
after it failed, however fresh its entries look. A failed scan waits
:data:`KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS` before its retry, and that wait
doubles with each failure in a row up to the refresh age, so an integration
that keeps failing costs a few attempts a day, not one an hour.
Separately, who may read workspace knowledge is refreshed every
:data:`KNOWLEDGE_READERS_REFRESH_SECONDS` with one cheap member-list call.

A process runs one service. The gateway installs its own
(:func:`install_scan_service`) and runs it on a background thread; commands
that ask for a scan (``/knowledge scan``, ``opensre knowledge scan``) poke
that one instead of scanning inside a chat turn, and build their own only
where none is installed (a laptop CLI). Scans run one at a time, in the
organization's storage scope, and never raise to the caller.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from config.constants import (
    KNOWLEDGE_READERS_REFRESH_SECONDS,
    KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS,
    KNOWLEDGE_SCAN_MAX_AGE_SECONDS,
)
from config.constants.organization import organization_id
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.domain.knowledge import ScanOutcome, Summarize, knowledge_enabled, load_workspace_entry
from core.domain.memory.stamps import parse_stamp
from integrations.knowledge_scans.registry import SCANNERS, Scanner
from integrations.knowledge_scans.summarizer import llm_summarizer

logger = logging.getLogger(__name__)

#: Actor recorded for writes a scan makes; it acts for no member.
SCAN_ACTOR = "knowledge-scan"


class KnowledgeScanService:
    """Which scans are due, running them, and what the last run of each did."""

    def __init__(
        self,
        *,
        organization: str = "",
        scanners: Sequence[Scanner] = SCANNERS,
        summarizer_factory: Callable[[], Summarize | None] = llm_summarizer,
        clock: Callable[[], float] = time.time,
        max_age_seconds: float = KNOWLEDGE_SCAN_MAX_AGE_SECONDS,
        retry_after_seconds: float = KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS,
    ) -> None:
        self._organization = organization
        self._scanners = {scanner.service: scanner for scanner in scanners}
        self._summarizer_factory = summarizer_factory
        self._clock = clock
        self._max_age = max_age_seconds
        self._retry_after = retry_after_seconds
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._forced: set[str] = set()
        self._last_attempt: dict[str, float] = {}
        #: Failed scans in a row, per service; a success resets it.
        self._failures: dict[str, int] = {}
        self._outcomes: dict[str, ScanOutcome] = {}
        self._readers_refreshed_at: dict[str, float] = {}

    @property
    def services(self) -> tuple[str, ...]:
        """Every service this service can scan."""
        return tuple(self._scanners)

    def request_rescan(self, service: str) -> bool:
        """Scan ``service`` on the next pass whatever its age; ``False`` for an unknown service."""
        return not self.request_scans([service], force=False)

    def request_scans(self, services: Sequence[str], *, force: bool) -> list[str]:
        """Rescan ``services`` (every one when empty and ``force``) on the next pass, and wake it.

        Returns the names this service cannot scan.
        """
        unknown = [name for name in services if name not in self._scanners]
        named = [name for name in services if name in self._scanners]
        with self._lock:
            self._forced.update(named or (self._scanners if force else ()))
        self.poke()
        return unknown

    def poke(self) -> None:
        """Wake the background loop now (an integration was connected or a rescan asked)."""
        self._wake.set()

    def status_rows(self) -> list[dict[str, object]]:
        """The last outcome of each scan this process ran, for the web app mirror."""
        with self._lock:
            outcomes = sorted(self._outcomes.values(), key=lambda outcome: outcome.service)
        return [
            {
                "service": outcome.service,
                "ok": outcome.ok,
                "written": list(outcome.written),
                "error": outcome.error,
                **({"details": dict(outcome.details)} if outcome.details else {}),
            }
            for outcome in outcomes
        ]

    def _scope(self) -> AbstractContextManager[Any]:
        if not self._organization:
            return nullcontext()
        return bound_storage_scope(
            StorageScope(principal=Principal.org(self._organization), actor=Actor(SCAN_ACTOR))
        )

    def _retry_wait(self, failures: int) -> float:
        """Seconds before the next attempt: doubling per failure in a row, up to the refresh age."""
        if failures <= 1:
            return self._retry_after
        return float(min(self._retry_after * 2 ** (failures - 1), self._max_age))

    def _entries_due(self, scanner: Scanner, now: float) -> bool:
        """Whether any entry the scan writes is missing or past the refresh age."""
        for slug in scanner.writes:
            entry = load_workspace_entry(slug)
            written = parse_stamp(entry.updated_at) if entry is not None else None
            if written is None or now - written.timestamp() >= self._max_age:
                return True
        return False

    def _is_due(self, scanner: Scanner, now: float) -> bool:
        with self._lock:
            if scanner.service in self._forced:
                return True
            last_attempt = self._last_attempt.get(scanner.service)
            failures = self._failures.get(scanner.service, 0)
        if last_attempt is not None and now - last_attempt < self._retry_wait(failures):
            return False
        # A failure can leave every entry in place (the last good knowledge
        # kept), so it is retried on its own account.
        return failures > 0 or self._entries_due(scanner, now)

    def run_due(self) -> list[ScanOutcome]:
        """Refresh readers when due, then run every due scan once; never raises."""
        now = self._clock()
        with self._scope():
            available = [scanner for scanner in self._scanners.values() if _available(scanner)]
            for scanner in available:
                self._refresh_readers(scanner, now)
            due = [scanner for scanner in available if self._is_due(scanner, now)]
            if not due:
                return []
            summarize = self._summarizer_factory()
            return [self._run(scanner, summarize) for scanner in due]

    def scan_now(
        self, services: Sequence[str], *, force: bool
    ) -> tuple[list[ScanOutcome], list[str]]:
        """Scan ``services`` (every one when empty and ``force``) plus whatever is due.

        Returns the outcomes and the names this service cannot scan.
        """
        unknown = self.request_scans(services, force=force)
        return self.run_due(), unknown

    def _refresh_readers(self, scanner: Scanner, now: float) -> None:
        if scanner.refresh_readers is None:
            return
        last = self._readers_refreshed_at.get(scanner.service)
        if last is not None and now - last < KNOWLEDGE_READERS_REFRESH_SECONDS:
            return
        self._readers_refreshed_at[scanner.service] = now
        try:
            if not scanner.refresh_readers():
                logger.info(
                    "[knowledge] %s readers kept: the refresh did not finish", scanner.service
                )
        except Exception as exc:  # noqa: BLE001 - the last recorded readers stay in force
            logger.warning(
                "[knowledge] %s readers refresh failed (%s)", scanner.service, type(exc).__name__
            )

    def _run(self, scanner: Scanner, summarize: Summarize | None) -> ScanOutcome:
        with self._lock:
            self._forced.discard(scanner.service)
            self._last_attempt[scanner.service] = self._clock()
        try:
            outcome = scanner.scan(summarize)
        except Exception as exc:  # noqa: BLE001 - one broken scan must not stop the others
            logger.warning("[knowledge] %s scan failed (%s)", scanner.service, type(exc).__name__)
            outcome = ScanOutcome(
                scanner.service, ok=False, error="The scan failed; it will retry."
            )
        if outcome.ok:
            logger.info("[knowledge] %s scan wrote %s", scanner.service, ", ".join(outcome.written))
        with self._lock:
            self._outcomes[scanner.service] = outcome
            self._failures[scanner.service] = (
                0 if outcome.ok else self._failures.get(scanner.service, 0) + 1
            )
        return outcome

    def run_forever(self, stopped: threading.Event) -> None:
        """Run due scans until ``stopped``; a :meth:`poke` starts the next pass early."""
        while not stopped.is_set():
            # Clear before the pass: a poke that lands while it runs stays set
            # and starts the next pass at once instead of being lost.
            self._wake.clear()
            # A stop pokes after setting ``stopped``; if that poke landed just
            # before the clear it is gone, so look again rather than sleep.
            if stopped.is_set():
                break
            try:
                self.run_due()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive after one bad pass
                logger.warning("[knowledge] scan pass failed (%s)", type(exc).__name__)
            self._wake.wait(KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS)


def _available(scanner: Scanner) -> bool:
    try:
        return scanner.available()
    except Exception:  # noqa: BLE001 - an unreadable integration store means "not connected"
        logger.debug("[knowledge] %s availability check failed", scanner.service, exc_info=True)
        return False


_installed: KnowledgeScanService | None = None
_install_lock = threading.Lock()


def install_scan_service(service: KnowledgeScanService | None) -> None:
    """Make ``service`` the one this process's commands poke (``None`` uninstalls it)."""
    global _installed
    with _install_lock:
        _installed = service


def installed_scan_service() -> KnowledgeScanService | None:
    """The service a long-running process (the gateway) runs, or ``None``."""
    with _install_lock:
        return _installed


def scan_on_request(
    services: Sequence[str], *, force: bool
) -> tuple[list[ScanOutcome] | None, list[str]]:
    """Answer a member's scan request; ``(outcomes or None, unknown names)``.

    In a process that runs the scan loop (the gateway), the request is handed
    to that loop and ``None`` comes back: the scan runs in the background,
    with its backoff, never inside the chat turn that asked. Elsewhere (a
    laptop CLI) it runs here.
    """
    running = installed_scan_service()
    if running is not None:
        return None, running.request_scans(services, force=force)
    return KnowledgeScanService(organization=organization_id()).scan_now(services, force=force)


def knowledge_scan_service_from_environment() -> KnowledgeScanService | None:
    """A scan service for this process, or ``None`` when workspace knowledge is off."""
    if not knowledge_enabled():
        return None
    return KnowledgeScanService(organization=organization_id())


__all__ = [
    "KnowledgeScanService",
    "install_scan_service",
    "installed_scan_service",
    "knowledge_scan_service_from_environment",
    "scan_on_request",
]
