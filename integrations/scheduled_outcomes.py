"""Retain tool-verified work outcomes across a scheduled agent turn."""

from threading import Lock
from typing import Any

from pydantic import ValidationError

from core.agent_harness import TurnResult
from core.tool import ToolExecutionRequest, ToolExecutionResult
from infrastructure.scheduling.scheduler.outcomes import WorkOutcome, WorkStatus
from infrastructure.scheduling.scheduler.types import TaskReport


class ScheduledOutcomes:
    """Keep the latest verified outcome for each operation, including recovered failures."""

    def __init__(self) -> None:
        self._outcomes: dict[str, WorkOutcome] = {}
        self._lock = Lock()

    def observe(self, request: ToolExecutionRequest, result: ToolExecutionResult) -> None:
        """Record producer-owned structured evidence without interpreting report prose."""
        payload: Any = result.details
        if not isinstance(payload, dict) or "work_outcome" not in payload:
            return
        try:
            outcome = WorkOutcome.model_validate(payload["work_outcome"])
        except ValidationError:
            outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="invalid_work_outcome")
        key = outcome.operation or request.tool_call.name
        with self._lock:
            self._outcomes[key] = outcome

    def report(self, turn: TurnResult, *, agent_mode: bool) -> TaskReport:
        """Require work evidence for agent tasks and a complete response for report tasks."""
        text = turn.primary_response_text
        with self._lock:
            outcomes = tuple(self._outcomes.values())
        # A block no retry can clear is a verified fact about the target, so it
        # outranks how the turn ended: a cancel or iteration cap after the tool
        # reported ``pr_not_open`` must still pause the schedule, or the next
        # tick fires at a target already known to be stuck.
        terminal_block = next((item for item in outcomes if item.terminal_block), None)
        stop_schedule = terminal_block is not None
        if terminal_block is not None:
            outcome = terminal_block
        elif turn.cancelled or turn.action_result.hit_iteration_cap:
            outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="turn_interrupted")
        elif not text:
            outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="report_missing")
        elif not agent_mode:
            outcome = WorkOutcome(status=WorkStatus.SUCCEEDED)
        else:
            unresolved = next((item for item in outcomes if not item.completed), None)
            if unresolved is not None:
                outcome = unresolved
            elif outcomes:
                outcome = WorkOutcome(
                    status=WorkStatus.NOOP
                    if all(item.status is WorkStatus.NOOP for item in outcomes)
                    else WorkStatus.SUCCEEDED,
                    evidence={"operations": [item.model_dump(mode="json") for item in outcomes]},
                )
            else:
                outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="work_unverified")
        if stop_schedule:
            text += "\n\nSchedule paused: the repair target requires attention before retrying."
        return TaskReport(text, outcome=outcome, stop_schedule=stop_schedule)
