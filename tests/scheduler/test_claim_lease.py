"""Tests for process-level scheduler claim renewal."""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta

from infrastructure.scheduling.scheduler.claim_lease import ClaimLeaseRenewer
from infrastructure.scheduling.scheduler.storage import ExecutionClaim

_SYNC_TIMEOUT_SECONDS = 5.0


def _claim(task_id: str, *, expires_in: float = 1.0) -> ExecutionClaim:
    return ExecutionClaim(
        task_id=task_id,
        fire_time="2026-01-01T09:00Z",
        attempt=1,
        owner_token=f"owner-{task_id}",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    )


def _wait_until(predicate: Callable[[], bool], timeout: float = _SYNC_TIMEOUT_SECONDS) -> bool:
    done = threading.Event()
    deadline = datetime.now(UTC) + timedelta(seconds=timeout)
    while datetime.now(UTC) < deadline:
        if predicate():
            return True
        done.wait(0.005)
    return False


def test_clock_sampling_pause_does_not_extend_ownership() -> None:
    claim = _claim("clock-pause", expires_in=10)
    elapsed = 0.0

    def utc_now() -> datetime:
        nonlocal elapsed
        sampled = claim.lease_expires_at - timedelta(seconds=10)
        # Model descheduling after sampling UTC but before returning to the caller.
        elapsed = 20.0
        return sampled

    renewer = ClaimLeaseRenewer(
        monotonic=lambda: elapsed,
        utc_now=utc_now,
        renewal_interval_seconds=60,
    )
    with renewer.hold(claim) as ownership:
        assert not ownership.valid()


def test_concurrent_claims_are_renewed_in_one_batch() -> None:
    first = _claim("first")
    second = _claim("second")
    batches: list[tuple[ExecutionClaim, ...]] = []
    renewed = threading.Event()

    def renew(
        claims: Collection[ExecutionClaim],
    ) -> Mapping[ExecutionClaim, datetime]:
        batch = tuple(claims)
        batches.append(batch)
        renewed.set()
        expiry = datetime.now(UTC) + timedelta(seconds=1)
        return dict.fromkeys(batch, expiry)

    renewer = ClaimLeaseRenewer(renew=renew, renewal_interval_seconds=0.05)
    with renewer.hold(first) as first_ownership, renewer.hold(second) as second_ownership:
        assert renewed.wait(_SYNC_TIMEOUT_SECONDS)
        assert _wait_until(lambda: len(batches) >= 1)
        assert set(batches[0]) == {first, second}
        assert first_ownership.valid()
        assert second_ownership.valid()


def test_fenced_renewal_loses_only_the_rejected_claim() -> None:
    rejected = _claim("rejected")
    retained = _claim("retained")
    renewed = threading.Event()

    def renew(
        claims: Collection[ExecutionClaim],
    ) -> Mapping[ExecutionClaim, datetime]:
        renewed.set()
        expiry = datetime.now(UTC) + timedelta(seconds=1)
        return {claim: expiry for claim in claims if claim == retained}

    renewer = ClaimLeaseRenewer(renew=renew, renewal_interval_seconds=0.01)
    with renewer.hold(rejected) as rejected_ownership, renewer.hold(retained) as retained_ownership:
        assert renewed.wait(_SYNC_TIMEOUT_SECONDS)
        assert _wait_until(lambda: not rejected_ownership.valid())
        assert retained_ownership.valid()


def test_transient_renewal_error_recovers_before_confirmed_deadline() -> None:
    claim = _claim("transient")
    attempts = 0
    recovered = threading.Event()

    def renew(
        claims: Collection[ExecutionClaim],
    ) -> Mapping[ExecutionClaim, datetime]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary database failure")
        recovered.set()
        expiry = datetime.now(UTC) + timedelta(seconds=1)
        return dict.fromkeys(claims, expiry)

    renewer = ClaimLeaseRenewer(renew=renew, renewal_interval_seconds=0.01)
    with renewer.hold(claim) as ownership:
        assert recovered.wait(_SYNC_TIMEOUT_SECONDS)
        assert attempts >= 2
        assert ownership.valid()


def test_persistent_renewal_error_expires_the_local_ownership() -> None:
    claim = _claim("persistent", expires_in=0.08)
    attempted = threading.Event()

    def renew(
        _claims: Collection[ExecutionClaim],
    ) -> Mapping[ExecutionClaim, datetime]:
        attempted.set()
        raise RuntimeError("database unavailable")

    renewer = ClaimLeaseRenewer(renew=renew, renewal_interval_seconds=0.01)
    with renewer.hold(claim) as ownership:
        assert attempted.wait(_SYNC_TIMEOUT_SECONDS)
        assert _wait_until(lambda: not ownership.valid())


def test_renewal_loop_stops_after_the_last_claim_exits() -> None:
    claim = _claim("cleanup")
    renewed = threading.Event()

    def renew(
        claims: Collection[ExecutionClaim],
    ) -> Mapping[ExecutionClaim, datetime]:
        renewed.set()
        expiry = datetime.now(UTC) + timedelta(seconds=1)
        return dict.fromkeys(claims, expiry)

    renewer = ClaimLeaseRenewer(renew=renew, renewal_interval_seconds=0.01)
    with renewer.hold(claim):
        assert renewed.wait(_SYNC_TIMEOUT_SECONDS)

    assert _wait_until(lambda: renewer._thread is None)  # noqa: SLF001
