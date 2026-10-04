"""Retain tool-verified work outcomes across a scheduled agent turn."""

from threading import Lock
from typing import Any

from pydantic import ValidationError

from config.constants.scheduler import WORK_UNVERIFIED_ERROR_KIND
from core.agent_harness import TurnResult
from core.tool import ToolExecutionRequest, ToolExecutionResult
from infrastructure.scheduling.scheduler.outcomes import WorkOutcome, WorkStatus
from infrastructure.scheduling.scheduler.types import TaskReport


class ScheduledOutcomes:
    """Keep the latest verified outcome for each operation, including recovered failures.

    A loop bound to one PR or branch pauses when that target can never be repaired.
    An unbound sweep skips such a target and keeps its schedule for the others.
    """

    def __init__(self, *, bound_target: bool = True) -> None:
        self._outcomes: dict[str, WorkOutcome] = {}
        self._lock = Lock()
        self._bound_target = bound_target
        #: A tool ran and failed without a work outcome, so nothing vouches for it.
        self._unverified_failure = False

    def observe(self, request: ToolExecutionRequest, result: ToolExecutionResult) -> None:
        """Record producer-owned structured evidence without interpreting report prose."""
        payload: Any = result.details
        if not isinstance(payload, dict) or "work_outcome" not in payload:
            if result.is_error:
                with self._lock:
                    self._unverified_failure = True
            return
        try:
            outcome = WorkOutcome.model_validate(payload["work_outcome"])
        except ValidationError:
            outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="invalid_work_outcome")
        key = outcome.operation or request.tool_call.name
        with self._lock:
            self._outcomes[key] = outcome

    def report(self, turn: TurnResult, *, agent_mode: bool, text: str | None = None) -> TaskReport:
        """Require work evidence for agent tasks and a complete response for report tasks.

        ``text`` is the reply to deliver when the runner kept part of the turn's
        reply back; it defaults to the whole reply.
        """
        text = turn.primary_response_text if text is None else text
        with self._lock:
            outcomes = tuple(self._outcomes.values())
            unverified_failure = self._unverified_failure
        # A sweep picks its targets each tick, so a fork or closed PR it reached
        # is one ineligible target, not a reason to stop repairing the rest.
        sweep = not self._bound_target
        skipped = tuple(item for item in outcomes if sweep and item.terminal_block)
        work = tuple(item for item in outcomes if not (sweep and item.terminal_block))
        # A block no retry can clear is a verified fact about the target, so it
        # outranks how the turn ended: a cancel or iteration cap after the tool
        # reported ``pr_not_open`` must still pause the schedule, or the next
        # tick fires at a target already known to be stuck.
        terminal_block = next((item for item in work if item.terminal_block), None)
        stop_schedule = terminal_block is not None
        # A quiet agent tick replies with only its note: an empty body is the
        # expected report once its tools verified there was nothing to do, and no
        # other tool failed where nobody would see it.
        verified_noop = (
            agent_mode
            and not unverified_failure
            and bool(outcomes)
            and all(item.status is WorkStatus.NOOP for item in work)
        )
        if terminal_block is not None:
            outcome = terminal_block
        elif turn.cancelled or turn.action_result.hit_iteration_cap:
            outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="turn_interrupted")
        elif not text and not verified_noop:
            outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="report_missing")
        elif not agent_mode:
            outcome = WorkOutcome(status=WorkStatus.SUCCEEDED)
        else:
            unresolved = next((item for item in work if not item.completed), None)
            if unresolved is not None:
                outcome = unresolved
            elif outcomes:
                evidence: dict[str, Any] = {
                    "operations": [item.model_dump(mode="json") for item in work]
                }
                if skipped:
                    evidence["skipped"] = [item.model_dump(mode="json") for item in skipped]
                outcome = WorkOutcome(
                    status=WorkStatus.NOOP
                    if all(item.status is WorkStatus.NOOP for item in work)
                    else WorkStatus.SUCCEEDED,
                    evidence=evidence,
                )
            else:
                outcome = WorkOutcome(
                    status=WorkStatus.INCOMPLETE, error_kind=WORK_UNVERIFIED_ERROR_KIND
                )
        if stop_schedule:
            text += "\n\nSchedule paused: the repair target requires attention before retrying."
        return TaskReport(text, outcome=outcome, stop_schedule=stop_schedule)
