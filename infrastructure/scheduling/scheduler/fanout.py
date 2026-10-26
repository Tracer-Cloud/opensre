"""Deliver one built message to a run's destinations concurrently.

Every destination is dispatched on a bounded worker pool and owns its own retry
loop, so a degraded provider retries in its own thread instead of holding up the
healthy ones, and a retry never re-sends to a destination that already
succeeded. Outcomes come back in plan order regardless of who finished first, so
run history and the message-id/error text a run persists are deterministic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from infrastructure.scheduling.scheduler.delivery_plan import DeliveryPlan, DeliveryTarget
from infrastructure.scheduling.scheduler.types import DeliveryOutcome, DeliveryStatus

logger = logging.getLogger(__name__)

#: Upper bound on concurrent deliveries; a run rarely has more destinations.
_MAX_DELIVERY_WORKERS = 8

#: Attempts per destination when a task is configured to fan out. A transient
#: outage on one channel must not permanently miss the tick.
_MAX_DELIVERY_ATTEMPTS = 3

#: Delivers one message to one destination, returning ``(ok, error, message_id)``.
DeliverOne = Callable[[DeliveryTarget, str], tuple[bool, str, str]]


@dataclass(frozen=True, slots=True)
class FanOutResult:
    """Per-destination outcomes for one run, in plan order."""

    outcomes: tuple[DeliveryOutcome, ...] = ()
    status: DeliveryStatus = DeliveryStatus.FAILED
    #: Why nothing was attempted, when the run had no usable destination.
    reason: str = ""

    def message_id(self) -> str:
        """The delivered message id, labelled by destination when fanning out.

        A run with one destination reports that adapter's id verbatim; only a
        fan-out needs the destination prefix to stay unambiguous.
        """
        if len(self.outcomes) == 1:
            return self.outcomes[0].message_id if self.outcomes[0].ok else ""
        return ", ".join(
            f"{outcome.label()}:{outcome.message_id}" if outcome.message_id else outcome.label()
            for outcome in self.outcomes
            if outcome.ok
        )

    def error(self) -> str:
        """Failure text, empty when every destination was delivered to."""
        if not self.outcomes:
            return self.reason
        if len(self.outcomes) == 1:
            return self.outcomes[0].error if not self.outcomes[0].ok else ""
        failures = "; ".join(
            f"{outcome.label()}: {outcome.error}" for outcome in self.outcomes if not outcome.ok
        )
        if not failures:
            return ""
        if self.status is DeliveryStatus.PARTIAL:
            return f"partial delivery: {failures}"
        return failures


def deliver_plan(plan: DeliveryPlan, message: str, deliver_one: DeliverOne) -> FanOutResult:
    """Deliver ``message`` to every destination in ``plan`` concurrently."""
    if plan.error:
        return FanOutResult(status=DeliveryStatus.FAILED, reason=plan.error)
    if not plan.targets:
        return FanOutResult(
            status=DeliveryStatus.FAILED, reason="No delivery destination configured"
        )

    attempts = _MAX_DELIVERY_ATTEMPTS if plan.fanned_out else 1
    outcomes: tuple[DeliveryOutcome, ...]
    if len(plan.targets) == 1:
        outcomes = (_deliver_target(plan.targets[0], message, deliver_one, attempts),)
    else:
        outcomes = _deliver_concurrently(plan.targets, message, deliver_one, attempts)
    return FanOutResult(outcomes=outcomes, status=_status_for(outcomes))


def _deliver_concurrently(
    targets: tuple[DeliveryTarget, ...],
    message: str,
    deliver_one: DeliverOne,
    attempts: int,
) -> tuple[DeliveryOutcome, ...]:
    """Run one delivery task per destination and collect results in plan order.

    A destination the pool refused is reported as failed rather than retried on
    this thread: it may already have been submitted, and a duplicate post is
    worse than a missed one that the next tick or ``/loops run`` recovers.
    """
    workers = min(_MAX_DELIVERY_WORKERS, len(targets))
    futures: dict[int, Future[DeliveryOutcome]] = {}
    submit_error = ""
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scheduler-delivery") as pool:
        for index, target in enumerate(targets):
            try:
                futures[index] = pool.submit(
                    _deliver_target, target, message, deliver_one, attempts
                )
            except RuntimeError as exc:
                submit_error = f"delivery pool unavailable: {type(exc).__name__}"
                logger.warning("Could not submit delivery to %s: %s", target.label(), exc)

    return tuple(
        _resolved(futures[index], target)
        if index in futures
        else _failure(target, submit_error, attempts=0)
        for index, target in enumerate(targets)
    )


def _resolved(future: Future[DeliveryOutcome], target: DeliveryTarget) -> DeliveryOutcome:
    """Return a completed future's outcome, or a failure outcome if it raised."""
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Delivery to %s raised", target.label(), exc_info=True)
        return _failure(target, type(exc).__name__, attempts=0)


def _deliver_target(
    target: DeliveryTarget,
    message: str,
    deliver_one: DeliverOne,
    attempts: int,
) -> DeliveryOutcome:
    """Deliver to one destination, retrying only that destination on failure."""
    error = "delivery was not attempted"
    for attempt in range(1, attempts + 1):
        ok, error, message_id = deliver_one(target, message)
        if ok:
            return DeliveryOutcome(
                provider=target.provider,
                chat_id=target.chat_id,
                ok=True,
                message_id=message_id,
                attempts=attempt,
            )
    return _failure(target, error, attempts=attempts)


def _failure(target: DeliveryTarget, error: str, *, attempts: int) -> DeliveryOutcome:
    return DeliveryOutcome(
        provider=target.provider,
        chat_id=target.chat_id,
        ok=False,
        error=error,
        attempts=attempts,
    )


def _status_for(outcomes: tuple[DeliveryOutcome, ...]) -> DeliveryStatus:
    """Classify a run as fully, partly, or not delivered."""
    delivered = sum(1 for outcome in outcomes if outcome.ok)
    if delivered == 0:
        return DeliveryStatus.FAILED
    if delivered == len(outcomes):
        return DeliveryStatus.SUCCESS
    return DeliveryStatus.PARTIAL


__all__ = [
    "DeliverOne",
    "FanOutResult",
    "deliver_plan",
]
