"""Session state a host reads and sets around a turn.

Trust mode, the terminal, auto-command and turn-outcome hints, resume notes
from an interrupted turn, a turn parked behind integration setup, transcript
compaction, and the capabilities a host withholds.
"""

from __future__ import annotations

from core.agent_harness.session.capabilities import withhold_capabilities
from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.session.pending_offer import (
    PendingScheduleOffer,
    clear_competing_pending_offers,
)
from core.agent_harness.session.persistence.wal_recovery import format_recovery_note
from core.agent_harness.session.setup_resume import (
    SetupResume,
    arm_setup_resume,
    clear_setup_resume,
    pending_setup_resume,
    take_setup_resume,
)
from core.agent_harness.session.terminal_access import (
    clear_pending_autosubmit,
    exclusive_stdin_active,
    pop_turn_outcome_hint,
    session_terminal,
    set_auto_command,
    set_turn_outcome_hint,
    trust_mode_enabled,
)
from core.agent_harness.turns.transcript_compaction import compact_session_branch, should_compact

__all__ = [
    "PendingScheduleOffer",
    "PendingUserChoice",
    "SetupResume",
    "arm_setup_resume",
    "clear_competing_pending_offers",
    "clear_pending_autosubmit",
    "clear_setup_resume",
    "compact_session_branch",
    "exclusive_stdin_active",
    "format_recovery_note",
    "pending_setup_resume",
    "pop_turn_outcome_hint",
    "session_terminal",
    "set_auto_command",
    "set_turn_outcome_hint",
    "should_compact",
    "take_setup_resume",
    "trust_mode_enabled",
    "withhold_capabilities",
]
