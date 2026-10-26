"""Tests for the process-wide Gateway turn gate."""

from __future__ import annotations

import logging
import threading

import pytest

from gateway.tests.runtime.concurrency_limited_handler import (
    ConcurrencyLimitedTurnHandler,
)
from infrastructure.deployment.contracts.models import SizeProfile
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.turn_host.concurrency import TurnConcurrencyGate


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (SizeProfile.SMALL, 1),
        (SizeProfile.MEDIUM, 2),
        (SizeProfile.LARGE, 4),
    ],
)
def test_profile_limits(profile: SizeProfile, expected: int) -> None:
    gate = TurnConcurrencyGate.for_profile(profile)
    acquired = [gate.try_acquire() for _ in range(expected + 1)]

    assert acquired == [True] * expected + [False]


def test_chat_handler_releases_capacity_in_finally() -> None:
    gate = TurnConcurrencyGate(1)

    def failing_handler(*_args: object) -> None:
        raise RuntimeError("sensitive detail")

    handler = ConcurrencyLimitedTurnHandler(handler=failing_handler, gate=gate)

    with pytest.raises(RuntimeError, match="sensitive detail"):
        handler("hello", object(), object(), logging.getLogger("test"))  # type: ignore[arg-type]

    assert gate.try_acquire() is True
    gate.release()


def test_chat_handler_refuses_excess_turn_without_calling_handler() -> None:
    gate = TurnConcurrencyGate(1)
    entered = threading.Event()
    release = threading.Event()
    finalized: list[str] = []

    def blocking_handler(*_args: object) -> None:
        entered.set()
        release.wait(1)

    class Sink:
        def finalize(self, answer: str) -> None:
            finalized.append(answer)

    handler = ConcurrencyLimitedTurnHandler(handler=blocking_handler, gate=gate)
    first = threading.Thread(
        target=handler,
        args=("one", object(), Sink(), logging.getLogger("test")),
    )
    first.start()
    assert entered.wait(1)

    handler("two", object(), Sink(), logging.getLogger("test"))  # type: ignore[arg-type]
    release.set()
    first.join(1)

    assert finalized == ["OpenSRE is at capacity. Please try again shortly."]


def test_gateway_turn_runner_gate_refuses_excess_without_second_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production path: capacity lives on TurnRunner itself."""
    from rich.console import Console

    from infrastructure.turn_host.turn_runner import TurnRunner

    gate = TurnConcurrencyGate(1)
    entered = threading.Event()
    release = threading.Event()
    finalized: list[str] = []
    ran: list[str] = []

    class Sink:
        def finalize(self, answer: str) -> None:
            finalized.append(answer)

    handler = TurnRunner(console=Console(force_terminal=False), gate=gate)

    def _fake_run(self, text, session, sink, logger, **_kwargs):  # noqa: ANN001
        # ``**_kwargs`` absorbs the caller-context keywords ``run`` forwards.
        _ = (self, session, sink, logger)
        ran.append(text)
        entered.set()
        release.wait(1)

    monkeypatch.setattr(TurnRunner, "_run_turn", _fake_run)

    first = threading.Thread(
        target=handler,
        args=("one", object(), Sink(), logging.getLogger("test")),
    )
    first.start()
    assert entered.wait(1)

    handler("two", object(), Sink(), logging.getLogger("test"))  # type: ignore[arg-type]
    release.set()
    first.join(1)

    assert ran == ["one"]
    assert finalized == ["OpenSRE is at capacity. Please try again shortly."]


def test_process_turn_gate_is_shared_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    from infrastructure.turn_host.concurrency import (
        process_turn_gate,
        reset_process_turn_gate_for_tests,
        set_process_turn_gate,
    )

    reset_process_turn_gate_for_tests()
    monkeypatch.setenv("OPENSRE_SIZE_PROFILE", "SMALL")
    first = process_turn_gate()
    second = process_turn_gate()
    assert first is second
    custom = TurnConcurrencyGate(2)
    set_process_turn_gate(custom)
    assert process_turn_gate() is custom
    reset_process_turn_gate_for_tests()


def test_scheduler_runner_waits_for_the_same_chat_capacity() -> None:
    gate = TurnConcurrencyGate(1)
    assert gate.try_acquire() is True  # active chat turn
    entered = threading.Event()
    result: list[str] = []

    def scheduled_runner(_payload: dict[str, object]) -> str:
        entered.set()
        return "done"

    bundle = SchedulerRunners(agent=scheduled_runner).gated(gate)
    thread = threading.Thread(
        target=lambda: result.append(bundle.agent({})),
    )
    thread.start()
    assert not entered.wait(0.05)

    gate.release()
    assert entered.wait(1)
    thread.join(1)

    assert result == ["done"]


def test_max_concurrent_turns_override_beats_the_size_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A deployment can raise concurrency on the same task without changing tier.
    from infrastructure.turn_host.concurrency import (
        process_turn_gate,
        reset_process_turn_gate_for_tests,
    )

    reset_process_turn_gate_for_tests()
    monkeypatch.setenv("OPENSRE_SIZE_PROFILE", "SMALL")  # profile would give 1
    monkeypatch.setenv("OPENSRE_MAX_CONCURRENT_TURNS", "3")
    assert process_turn_gate().limit == 3
    reset_process_turn_gate_for_tests()


def test_invalid_override_falls_back_to_the_size_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo (non-positive / unparseable) must not drop the gate below the tier.
    from infrastructure.turn_host.concurrency import configured_turn_limit

    monkeypatch.setenv("OPENSRE_SIZE_PROFILE", "SMALL")
    monkeypatch.setenv("OPENSRE_MAX_CONCURRENT_TURNS", "0")
    assert configured_turn_limit() == 1
    monkeypatch.setenv("OPENSRE_MAX_CONCURRENT_TURNS", "lots")
    assert configured_turn_limit() == 1
