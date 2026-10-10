"""Gateway-hosted bounded worker with cooperative cancellation and lease fencing."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from config.constants.triage import TRIAGE_POLL_SECONDS
from core.domain.alerts.triage.models import InvestigationClaim
from core.domain.alerts.triage.storage import TriageStore
from core.domain.alerts.triage.storage.store import ClaimLostError
from infrastructure.process.turn_capacity import TurnGate, waiting_turn_slot

Investigator = Callable[[InvestigationClaim, Callable[[], bool]], dict[str, Any]]


class TriageWorker:
    """One active investigation sharing capacity with other gateway turns."""

    def __init__(
        self,
        store: TriageStore,
        investigate: Investigator,
        gate: TurnGate,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.store = store
        self.investigate = investigate
        self.gate = gate
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start one supervised polling thread; shell lifetime is irrelevant."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="opensre-triage", daemon=True)
            self._thread.start()

    def stop(self, *, timeout: float = 5) -> bool:
        """Request cancellation, retaining a live lease until the runner returns."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        finished = self._thread is None or not self._thread.is_alive()
        if finished:
            self.store.stopped()
        return finished

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.store.heartbeat(active=None)
                # Take capacity before claiming so queue waiting does not spend model budget.
                with waiting_turn_slot(
                    self.gate, timeout_seconds=1, stop=self._stop.is_set
                ) as acquired:
                    if acquired and not self._stop.is_set():
                        claim = self.store.claim()
                        if claim is not None:
                            self._execute(claim)
            except Exception as exc:
                self.logger.error("triage worker failed (%s)", type(exc).__name__)
                self.store.heartbeat(active=None, failure=type(exc).__name__)
            self._stop.wait(TRIAGE_POLL_SECONDS)

    def _execute(self, claim: InvestigationClaim) -> None:
        done = threading.Event()
        cancelled = threading.Event()
        result: dict[str, Any] = {}
        failure: list[str] = []

        def stopped() -> bool:
            return self._stop.is_set() or cancelled.is_set()

        def run() -> None:
            try:
                result.update(self.investigate(claim, stopped))
            except ClaimLostError:
                cancelled.set()
            except Exception as exc:
                failure.append(type(exc).__name__)
                result.update(
                    {
                        "observed": "Investigation could not complete.",
                        "likely_cause": "Insufficient evidence",
                        "unknowns": type(exc).__name__,
                        "next_check": "Verify query access and model configuration.",
                    }
                )
            finally:
                done.set()

        thread = threading.Thread(target=run, name="opensre-triage-turn", daemon=True)
        thread.start()
        while not done.wait(TRIAGE_POLL_SECONDS):
            self.store.heartbeat(active=claim.id)
            if not self.store.renew(claim):
                cancelled.set()
        if not cancelled.is_set():
            self.store.finish(claim, result, failure=failure[0] if failure else None)
        self.store.heartbeat(active=None, failure=failure[0] if failure else None)
