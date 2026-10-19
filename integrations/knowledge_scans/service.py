"""Keep workspace knowledge current: scan each connected integration when it is due.

A scan is due when the integration is connected and its entry is missing (it
was just connected, or someone deleted the entry), older than
:data:`KNOWLEDGE_SCAN_MAX_AGE_SECONDS`, or a rescan was requested. A failed
scan waits :data:`KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS` before its retry, and
that wait doubles with each failure in a row up to the refresh age, so an
integration that keeps failing costs a few attempts a day, not one an hour.

The gateway runs :meth:`KnowledgeScanService.run_forever` on a background
thread and pokes it when the integration store changes; the CLI calls
:meth:`run_due` directly. Scans run one at a time, in the organization's
storage scope, and never raise to the caller.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterator, Sequence

from config.constants import (
    KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS,
    KNOWLEDGE_SCAN_MAX_AGE_SECONDS,
)
from config.constants.organization import organization_id
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.domain.knowledge import (
    ScanOutcome,
    Summarize,
    entry_age_seconds,
    integration_slug,
    knowledge_enabled,
    load_workspace_entry,
)
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
        self._scanners = tuple(scanners)
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

    @property
    def services(self) -> tuple[str, ...]:
        """Every service this service can scan."""
        return tuple(scanner.service for scanner in self._scanners)

    def request_rescan(self, service: str) -> bool:
        """Scan ``service`` on the next pass whatever its age; ``False`` for an unknown service."""
        if service not in self.services:
            return False
        with self._lock:
            self._forced.add(service)
        self.poke()
        return True

    def poke(self) -> None:
        """Wake the background loop now (an integration was connected or a rescan asked)."""
        self._wake.set()

    def last_outcomes(self) -> dict[str, ScanOutcome]:
        """The most recent outcome per service scanned by this process."""
        with self._lock:
            return dict(self._outcomes)

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

    @contextlib.contextmanager
    def _scope(self) -> Iterator[None]:
        if not self._organization:
            yield
            return
        scope = StorageScope(principal=Principal.org(self._organization), actor=Actor(SCAN_ACTOR))
        with bound_storage_scope(scope):
            yield

    def _is_due(self, scanner: Scanner, now: float) -> bool:
        with self._lock:
            if scanner.service in self._forced:
                return True
            last_attempt = self._last_attempt.get(scanner.service)
            failures = self._failures.get(scanner.service, 0)
        if last_attempt is not None and now - last_attempt < self._retry_wait(failures):
            return False
        entry = load_workspace_entry(integration_slug(scanner.service))
        if entry is None:
            return True
        age = entry_age_seconds(entry)
        return age is None or age >= self._max_age

    def _retry_wait(self, failures: int) -> float:
        """Seconds before the next attempt: doubling per failure in a row, up to the refresh age."""
        if failures <= 1:
            return self._retry_after
        return float(min(self._retry_after * 2 ** (failures - 1), self._max_age))

    def due(self) -> list[str]:
        """Services whose scan is due now (connected ones only)."""
        now = self._clock()
        with self._scope():
            return [
                scanner.service
                for scanner in self._scanners
                if _available(scanner) and self._is_due(scanner, now)
            ]

    def run_due(self) -> list[ScanOutcome]:
        """Run every due scan once; never raises."""
        outcomes: list[ScanOutcome] = []
        summarize: Summarize | None = None
        summarizer_built = False
        with self._scope():
            for scanner in self._scanners:
                if not _available(scanner) or not self._is_due(scanner, self._clock()):
                    continue
                if not summarizer_built:
                    summarize, summarizer_built = self._summarizer_factory(), True
                outcomes.append(self._run(scanner, summarize))
        return outcomes

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

    def run_forever(
        self,
        stopped: threading.Event,
        *,
        interval_seconds: float = KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS,
    ) -> None:
        """Run due scans until ``stopped``; a :meth:`poke` starts the next pass early."""
        while not stopped.is_set():
            # Clear before the pass: a poke that lands while it runs stays set
            # and starts the next pass at once instead of being lost.
            self._wake.clear()
            try:
                self.run_due()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive after one bad pass
                logger.warning("[knowledge] scan pass failed (%s)", type(exc).__name__)
            self._wake.wait(interval_seconds)


def _available(scanner: Scanner) -> bool:
    try:
        return scanner.available()
    except Exception:  # noqa: BLE001 - an unreadable integration store means "not connected"
        logger.debug("[knowledge] %s availability check failed", scanner.service, exc_info=True)
        return False


def knowledge_scan_service_from_environment() -> KnowledgeScanService | None:
    """The scan service for this process, or ``None`` when workspace knowledge is off."""
    if not knowledge_enabled():
        return None
    return KnowledgeScanService(organization=organization_id())


__all__ = [
    "SCAN_ACTOR",
    "KnowledgeScanService",
    "knowledge_scan_service_from_environment",
]
