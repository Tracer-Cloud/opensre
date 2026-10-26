"""Test-only capacity wrapper for arbitrary gateway callbacks.

Production chat uses :class:`~infrastructure.turn_host.turn_runner.TurnRunner`
with ``gate=``. This helper stays under ``gateway/tests/`` so it cannot be
mistaken for a second production turn-runner class (Wave C4 quarantine).
"""

from __future__ import annotations

import logging

from core.agent_harness.session import SessionCore
from infrastructure.turn_host.concurrency import TurnConcurrencyGate
from infrastructure.turn_host.turn_callback import TurnCallback
from infrastructure.turn_host.turn_output import TurnOutput


class ConcurrencyLimitedTurnHandler:
    """Capacity wrapper for arbitrary :data:`TurnCallback` callables."""

    def __init__(
        self,
        *,
        handler: TurnCallback,
        gate: TurnConcurrencyGate,
        busy_message: str = "OpenSRE is at capacity. Please try again shortly.",
    ) -> None:
        self._handler = handler
        self._gate = gate
        self._busy_message = busy_message

    def __call__(
        self,
        text: str,
        session: SessionCore,
        output: TurnOutput,
        logger: logging.Logger,
    ) -> None:
        if not self._gate.try_acquire():
            output.finalize(self._busy_message)
            return
        try:
            self._handler(text, session, output, logger)
        finally:
            self._gate.release()


def gated_callback(
    handler: TurnCallback,
    gate: TurnConcurrencyGate,
) -> TurnCallback:
    """Wrap an arbitrary callback with the shared capacity gate (tests only)."""
    return ConcurrencyLimitedTurnHandler(handler=handler, gate=gate)
