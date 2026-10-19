"""One opt-in watch tick: detect, investigate once, retain, and deliver."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Iterator

from filelock import FileLock

from core.telemetry.models import Evidence, EvidenceReader, Health, Measurement, Scope, WatchRule
from core.telemetry.storage.store import WatchStore

logger = logging.getLogger(__name__)
Investigator = Callable[[Scope, Measurement, Evidence], str]
Delivery = Callable[[str, str], bool]


class WatchRunner:
    def __init__(
        self,
        rule: WatchRule,
        reader: EvidenceReader,
        store: WatchStore,
        investigate: Investigator,
        deliver: Delivery,
    ) -> None:
        self.rule = rule
        self.reader = reader
        self.store = store
        self.investigate = investigate
        self.deliver = deliver

    def step(self, now: float) -> Health:
        """Serialize ticks across processes, including model calls and delivery acknowledgments."""
        with FileLock(str(self.store.path) + ".lock", timeout=0):
            self._finish_pending()
            scope = self.rule.scope(now)
            sample = None
            try:
                sample = self.reader.measure(scope, self.rule.metric)
                health = self.rule.classify(scope, sample)
            except Exception:
                logger.exception("Telemetry measurement failed")
                health = "unknown"
            self.store.observe(scope, health, self.rule, sample)
            self._finish_pending()
            self._deliver_pending()
            return health

    def _finish_pending(self) -> None:
        pending = self.store.pending()
        if pending is None:
            return
        episode, scope, sample = pending
        try:
            if sample is None:
                raise ValueError("Original detector measurement is unavailable.")
            evidence = self.reader.evidence(scope)
            report = self.investigate(scope, sample, evidence)
        except Exception:
            logger.exception("Telemetry investigation incomplete")
            report = (
                "Partial investigation: the detector opened this incident, but evidence or "
                "analysis was unavailable. Cause unknown; check the telemetry source and retry manually."
            )
        self.store.save_report(episode, scope, report)

    def _deliver_pending(self) -> None:
        for event in self.store.pending_events():
            try:
                sent = self.deliver(str(event["id"]), event["body"])
            except Exception:
                logger.exception("Telemetry report delivery failed")
                sent = False
            if not sent:
                break
            self.store.acknowledge(event["id"])


def poll(
    runner: WatchRunner,
    *,
    once: bool,
    now: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[Health]:
    """Poll on fixed deadlines; skip overrun windows rather than accumulating drift."""
    deadline = monotonic()
    interval = runner.rule.window_seconds
    while True:
        yield runner.step(now())
        if once:
            return
        deadline += interval
        current = monotonic()
        if deadline < current:
            deadline += math.ceil((current - deadline) / interval) * interval
        sleep(max(0, deadline - current))
