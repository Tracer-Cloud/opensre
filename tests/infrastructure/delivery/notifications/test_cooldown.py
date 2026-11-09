"""Tests for infrastructure.delivery.notifications.cooldown.CooldownGate."""

from __future__ import annotations

from infrastructure.delivery.notifications.cooldown import CooldownGate


def test_first_reservation_succeeds() -> None:
    gate = CooldownGate(cooldown_seconds=300.0)
    assert gate.try_reserve("key", now=100.0) is None


def test_second_reservation_within_cooldown_is_suppressed() -> None:
    gate = CooldownGate(cooldown_seconds=300.0)
    assert gate.try_reserve("key", now=100.0) is None
    remaining = gate.try_reserve("key", now=200.0)
    assert remaining is not None
    assert remaining == 200.0


def test_second_reservation_after_cooldown_succeeds() -> None:
    gate = CooldownGate(cooldown_seconds=300.0)
    assert gate.try_reserve("key", now=100.0) is None
    assert gate.try_reserve("key", now=450.0) is None


def test_cooldown_is_per_key() -> None:
    gate = CooldownGate(cooldown_seconds=300.0)
    assert gate.try_reserve("a", now=100.0) is None
    assert gate.try_reserve("b", now=110.0) is None


def test_reservation_happens_before_result_is_used() -> None:
    """A suppressed call must not reset the reservation for the key."""
    gate = CooldownGate(cooldown_seconds=300.0)
    assert gate.try_reserve("key", now=100.0) is None
    gate.try_reserve("key", now=150.0)  # suppressed, must not re-arm
    remaining = gate.try_reserve("key", now=200.0)
    assert remaining is not None
    assert remaining == 200.0
