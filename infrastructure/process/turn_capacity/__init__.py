"""Process-wide capacity gates: turn slots and the separate heavy-work slots."""

from infrastructure.process.turn_capacity.heavy_work import (
    HEAVY_WORK_BUSY_MESSAGE,
    HeavyWorkGate,
    configured_heavy_work_limit,
    heavy_work_slot,
    process_heavy_work_gate,
    reset_process_heavy_work_gate_for_tests,
)
from infrastructure.process.turn_capacity.slots import (
    TurnGate,
    queued_turn_slot,
    turn_slot,
    waiting_turn_slot,
)

__all__ = [
    "HEAVY_WORK_BUSY_MESSAGE",
    "HeavyWorkGate",
    "TurnGate",
    "configured_heavy_work_limit",
    "heavy_work_slot",
    "process_heavy_work_gate",
    "queued_turn_slot",
    "reset_process_heavy_work_gate_for_tests",
    "turn_slot",
    "waiting_turn_slot",
]
