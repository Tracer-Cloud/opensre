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
        if turn.cancelled or turn.action_result.hit_iteration_cap:
            outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="turn_interrupted")
        elif not text:
            outcome = WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="report_missing")
        elif not agent_mode:
            outcome = WorkOutcome(status=WorkStatus.SUCCEEDED)
        else:
            with self._lock:
                outcomes = tuple(self._outcomes.values())
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
        return TaskReport(text, outcome=outcome)
