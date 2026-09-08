"""Process-level lease renewal for active scheduler executions."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from infrastructure.scheduling.scheduler.storage import (
    ExecutionClaim,
    claim_renewal_interval_seconds,
    renew_claims,
)

logger = logging.getLogger(__name__)

RenewClaims = Callable[[Collection[ExecutionClaim]], Mapping[ExecutionClaim, datetime]]


@dataclass(slots=True)
class _LeaseState:
    confirmed_until: float
    renew_at: float
    lost: bool = False


class ClaimOwnership:
    """Expose whether one execution still owns its confirmed lease."""

    def __init__(
        self,
        state: _LeaseState,
        condition: threading.Condition,
        monotonic: Callable[[], float],
    ) -> None:
        self._state = state
        self._condition = condition
        self._monotonic = monotonic

    def valid(self) -> bool:
        """Return whether storage still confirms an unexpired fenced claim."""
        with self._condition:
            return not self._state.lost and self._monotonic() < self._state.confirmed_until


class ClaimLeaseRenewer:
    """Renew every active process claim from one shared background loop."""

    def __init__(
        self,
        *,
        renew: RenewClaims = renew_claims,
        renewal_interval_seconds: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._renew = renew
        self._renewal_interval_seconds = (
            claim_renewal_interval_seconds()
            if renewal_interval_seconds is None
            else renewal_interval_seconds
        )
        if self._renewal_interval_seconds <= 0:
            raise ValueError("renewal interval must be positive")
        self._monotonic = monotonic
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active: dict[ExecutionClaim, _LeaseState] = {}
        self._thread: threading.Thread | None = None

    @contextmanager
    def hold(self, claim: ExecutionClaim) -> Iterator[ClaimOwnership]:
        """Register an active claim and release it when execution exits."""
        confirmed_until = self._deadline_for(claim.lease_expires_at)
        state = _LeaseState(
            confirmed_until=confirmed_until,
            renew_at=min(
                confirmed_until,
                self._monotonic() + self._renewal_interval_seconds,
            ),
        )
        ownership = ClaimOwnership(state, self._condition, self._monotonic)
        with self._condition:
            if claim in self._active:
                raise RuntimeError("claim is already registered")
            self._active[claim] = state
            self._ensure_thread_locked()
            self._condition.notify_all()
        try:
            yield ownership
        finally:
            with self._condition:
                state.lost = True
                self._active.pop(claim, None)
                self._condition.notify_all()

    def _deadline_for(self, lease_expires_at: datetime) -> float:
        # Sample monotonic first so a pause between clocks cannot extend ownership.
        monotonic_now = self._monotonic()
        remaining = max(0.0, (lease_expires_at - self._utc_now()).total_seconds())
        return monotonic_now + remaining

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="scheduler-claim-renewal",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            with self._condition:
                if not self._active:
                    self._thread = None
                    return
                live_states = [state for state in self._active.values() if not state.lost]
                if not live_states:
                    self._condition.wait()
                    continue
                wake_at = min(min(state.renew_at, state.confirmed_until) for state in live_states)
                delay = min(
                    self._renewal_interval_seconds,
                    max(0.0, wake_at - self._monotonic()),
                )
                self._condition.wait(timeout=delay)
                claims = self._claims_for_renewal_locked()
            if not claims:
                continue
            try:
                renewed = self._renew(claims)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to renew scheduler execution claims", exc_info=True)
                with self._condition:
                    self._schedule_retry_locked(claims)
                continue
            with self._condition:
                for claim in claims:
                    state = self._active.get(claim)
                    if state is None:
                        continue
                    lease_expires_at = renewed.get(claim)
                    if lease_expires_at is None:
                        state.lost = True
                        logger.warning(
                            "Lost scheduler claim for task %s fire_time=%s attempt=%d",
                            claim.task_id,
                            claim.fire_time,
                            claim.attempt,
                        )
                    else:
                        state.confirmed_until = self._deadline_for(lease_expires_at)
                        state.renew_at = min(
                            state.confirmed_until,
                            self._monotonic() + self._renewal_interval_seconds,
                        )

    def _claims_for_renewal_locked(self) -> tuple[ExecutionClaim, ...]:
        now = self._monotonic()
        live: list[ExecutionClaim] = []
        renewal_due = False
        for claim, state in self._active.items():
            if state.lost:
                continue
            if now >= state.confirmed_until:
                state.lost = True
                logger.warning(
                    "Scheduler claim expired without confirmed renewal for task %s "
                    "fire_time=%s attempt=%d",
                    claim.task_id,
                    claim.fire_time,
                    claim.attempt,
                )
                continue
            live.append(claim)
            renewal_due = renewal_due or now >= state.renew_at
        return tuple(live) if renewal_due else ()

    def _schedule_retry_locked(self, claims: Collection[ExecutionClaim]) -> None:
        retry_at = self._monotonic() + self._renewal_interval_seconds
        for claim in claims:
            state = self._active.get(claim)
            if state is not None and not state.lost:
                state.renew_at = min(state.confirmed_until, retry_at)


default_claim_lease_renewer = ClaimLeaseRenewer()


__all__ = ["ClaimLeaseRenewer", "ClaimOwnership", "default_claim_lease_renewer"]
