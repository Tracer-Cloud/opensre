"""Resubmit the turn a missing integration parked, once setup has made its prerequisite hold.

The setup wizard's exit status says nothing about the credential
(``run_cli_command`` reports success for every interactive run), so the parked
skill's registered check runs again on freshly resolved integrations. A pass
replays the parked turn exactly once: a menu answer the way ``/choose`` submits
one, so it keeps its skill context; anything else as an ordinary turn. A miss
puts the setup menu back with a "still not connected" note. A turn no
registered check can confirm is dropped, never replayed: a cancelled wizard
must not bring the skill back.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from rich.console import Console
from rich.markup import escape

from config.constants.skill_prerequisites import prerequisite_service_label
from core.agent_harness.spi.integrations import resolve_and_cache_integrations
from core.agent_harness.spi.session_state import pending_setup_resume, take_setup_resume
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.runtime import Session
from tools.interactive_shell.actions.skill_prerequisite_gate import (
    queue_prerequisite_menu,
    setup_verdict,
)

logger = logging.getLogger(__name__)


class ResumeOutcome(StrEnum):
    """What :func:`resume_after_setup` did with the parked turn."""

    NOTHING_PARKED = "nothing_parked"
    REPLAYED = "replayed"
    SLOT_BUSY = "slot_busy"
    STILL_MISSING = "still_missing"
    DROPPED = "dropped"
    """No registered check can confirm the setup worked, so the turn is forgotten."""


def resume_after_setup(
    session: Session, console: Console, *, service: str | None = None
) -> ResumeOutcome:
    """Replay the parked turn when its prerequisite now holds; re-queue the setup menu when not.

    ``service`` is the integration that was just set up; a turn parked for
    another one stays parked. Only a registered check's positive answer
    replays; with no check that can answer, the turn is dropped. An auto-submit
    already queued is never replaced: the turn stays parked and nothing is queued.
    """
    record = pending_setup_resume(session)
    if record is None or (service is not None and record.service != service):
        return ResumeOutcome.NOTHING_PARKED
    verdict = setup_verdict(record.skill, record.service, resolve_and_cache_integrations(session))
    if verdict is None:
        take_setup_resume(session)
        logger.debug("Setup resume dropped: no check can confirm %s setup", record.service)
        return ResumeOutcome.DROPPED
    terminal = session.terminal
    if terminal.pending_prompt_autosubmit and terminal.pending_prompt_default:
        logger.debug("Setup resume deferred: another autosubmit is already queued")
        return ResumeOutcome.SLOT_BUSY
    if not verdict:
        queue_prerequisite_menu(session, record.service, still_missing=True)
        return ResumeOutcome.STILL_MISSING
    take_setup_resume(session)
    label = prerequisite_service_label(record.service)
    console.print(
        f"[{ui_theme.DIM}]{escape(label)} is connected — picking up where you left off.[/]"
    )
    if record.as_answer:
        # Exactly as ``/choose`` submits a pick: the answer keeps its skill.
        terminal.set_auto_command(record.text)
        terminal.awaiting_handoff_answer = True
    else:
        terminal.set_auto_prompt(record.text)
    return ResumeOutcome.REPLAYED


__all__ = ["ResumeOutcome", "resume_after_setup"]
