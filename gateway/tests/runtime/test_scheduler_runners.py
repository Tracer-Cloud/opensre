"""Scheduled agentic-loop runs share the gateway's turn gate.

The scheduler runs agentic loop tasks (Sentry digest, PostHog report, GitHub PR
sweep, manual loop). Each one costs a turn, so it must take the same capacity
gate chat turns take, exactly once, and give it back even when the run fails.

The runners are a value the host gates once at construction and passes into the
scheduler, so these also pin that gating is a pure value transform that cannot
compound the way the old read-modify-write on module state could.
"""

from __future__ import annotations

from typing import Any

import pytest

from infrastructure.scheduling.scheduler.runners import SchedulerRunners


class _CountingGate:
    """Stands in for ``TurnConcurrencyGate``, recording each acquire/release."""

    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    def acquire(self, *, timeout: float | None = None) -> bool:
        _ = timeout
        self.acquired += 1
        return True

    def release(self) -> None:
        self.released += 1


def test_a_scheduled_run_takes_the_gate_once_and_gives_it_back() -> None:
    # Arrange — a loop task's runner, gated the way the host gates it
    runs: list[dict[str, Any]] = []

    def runner(payload: dict[str, Any]) -> str:
        runs.append(payload)
        return "report body"

    gate = _CountingGate()
    bundle = SchedulerRunners(agent=runner).gated(gate)

    # Act
    result = bundle.agent({"source": "sentry_digest"})

    # Assert — one turn's worth of capacity, and the body reaches the scheduler
    assert (gate.acquired, gate.released) == (1, 1)
    assert runs == [{"source": "sentry_digest"}]
    assert result == "report body"


def test_the_gate_is_given_back_when_a_scheduled_run_fails() -> None:
    # Arrange — a loop task that raises partway through
    def failing_runner(payload: dict[str, Any]) -> str:
        _ = payload
        raise RuntimeError("digest failed")

    gate = _CountingGate()
    bundle = SchedulerRunners(agent=failing_runner).gated(gate)

    # Act
    with pytest.raises(RuntimeError, match="digest failed"):
        bundle.agent({"source": "sentry_digest"})

    # Assert — a failed run must not leak capacity
    assert (gate.acquired, gate.released) == (1, 1)


def test_gating_returns_a_new_value_and_does_not_mutate_the_original() -> None:
    """Gating a value returns a new bundle, so passing it around cannot compound.

    Gating used to read the registered runner, wrap it and write it back, so a
    second gating wrapped an already gated runner and one scheduled run cost two
    permits. As a value, ``gated`` yields a distinct bundle and the original is
    untouched, so one run costs one permit no matter how many times it is passed.
    """

    # Arrange
    def runner(payload: dict[str, Any]) -> str:
        _ = payload
        return "report body"

    gate = _CountingGate()
    bundle = SchedulerRunners(agent=runner)
    gated = bundle.gated(gate)

    # Act / Assert — a distinct value, and one run through it costs one permit
    assert gated is not bundle
    gated.agent({"source": "manual_loop"})
    assert (gate.acquired, gate.released) == (1, 1)


def test_a_run_without_a_model_turn_does_not_touch_the_gate() -> None:
    """A deterministic builder, such as the CI repair supervisor, must not hold a chat slot."""

    # Arrange — a bundle that knows which payloads run a model turn
    def runner(payload: dict[str, Any]) -> str:
        return str(payload["source"])

    gate = _CountingGate()
    bundle = SchedulerRunners(
        agent=runner, runs_model_turn=lambda payload: payload["source"] != "builder"
    ).gated(gate)

    # Act
    built = bundle.agent({"source": "builder"})
    digested = bundle.agent({"source": "sentry_digest"})

    # Assert — only the model turn cost a permit
    assert (built, digested) == ("builder", "sentry_digest")
    assert (gate.acquired, gate.released) == (1, 1)


def test_hosting_keeps_the_model_turn_predicate() -> None:
    # Arrange
    def runner(payload: dict[str, Any]) -> str:
        _ = payload
        return "report body"

    gate = _CountingGate()
    bundle = SchedulerRunners(agent=runner, runs_model_turn=lambda _payload: False)

    # Act
    hosted = bundle.hosted_by(lambda: "session").gated(gate)
    hosted.agent({"source": "builder"})

    # Assert
    assert (gate.acquired, gate.released) == (0, 0)
