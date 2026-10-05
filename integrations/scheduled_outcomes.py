"""Retain tool-verified work outcomes across a scheduled agent turn."""

from threading import Lock
from typing import Any

from pydantic import ValidationError

from config.constants.scheduler import STATELESS_LOOP_IDLE_REPLY, WORK_UNVERIFIED_ERROR_KIND
from core.agent_harness import TurnResult
from core.tool import ToolExecutionRequest, ToolExecutionResult
from infrastructure.scheduling.scheduler.outcomes import WorkOutcome, WorkStatus
from infrastructure.scheduling.scheduler.tool_actions import (
    call_failed,
    describe_tool_action,
    is_remote_write,
)
from infrastructure.scheduling.scheduler.types import TaskReport


class ScheduledOutcomes:
    """Keep the latest verified outcome for each operation, including recovered failures.

    A loop bound to one PR or branch pauses when that target can never be repaired.
    An unbound sweep skips such a target and keeps its schedule for the others.
    A stateless loop is also judged by what its tools changed: a successful push
    or GitHub write is done work, and the exact idle reply is a no-op unless a
    tool failed without reporting an outcome; there a nonzero shell exit counts
    as a failure too.
    """

    def __init__(self, *, bound_target: bool = True, stateless: bool = False) -> None:
        self._outcomes: dict[str, WorkOutcome] = {}
        self._lock = Lock()
        self._bound_target = bound_target
        #: A tool ran and failed without a work outcome, so nothing vouches for it.
        self._unverified_failure = False
        self._stateless = stateless
        #: The pushes and GitHub writes a stateless tick's tools made, as action lines.
        self._remote_writes: list[str] = []

    def observe(self, request: ToolExecutionRequest, result: ToolExecutionResult) -> None:
        """Record producer-owned structured evidence without interpreting report prose."""
        payload: Any = result.details
        if not isinstance(payload, dict) or "work_outcome" not in payload:
            if result.is_error or (self._stateless and call_failed(result)):
                with self._lock:
                    self._unverified_failure = True
            elif self._stateless and is_remote_write(request, result):
                action = describe_tool_action(request, result) or request.tool_call.name
                with self._lock:
                    self._remote_writes.append(action)
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
            remote_writes = tuple(self._remote_writes)
        # A stateless tick that found nothing to act on replies with one word and
        # delivers nothing; after a failed tool call that word proves nothing.
        idle_reply = self._stateless and _is_idle_reply(text)
        idle = idle_reply and not remote_writes and not unverified_failure
        if idle_reply and not remote_writes:
            text = ""
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
        elif not text and not (verified_noop or idle):
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
                if remote_writes:
                    evidence["actions"] = list(remote_writes)
                # A write the tools made is work even when every reported operation was a no-op.
                quiet = not remote_writes and all(item.status is WorkStatus.NOOP for item in work)
                outcome = WorkOutcome(
                    status=WorkStatus.NOOP if quiet else WorkStatus.SUCCEEDED,
                    evidence=evidence,
                )
            elif remote_writes:
                outcome = WorkOutcome(
                    status=WorkStatus.SUCCEEDED, evidence={"actions": list(remote_writes)}
                )
            elif idle:
                outcome = WorkOutcome(status=WorkStatus.NOOP)
            else:
                outcome = WorkOutcome(
                    status=WorkStatus.INCOMPLETE, error_kind=WORK_UNVERIFIED_ERROR_KIND
                )
        if stop_schedule:
            text += "\n\nSchedule paused: the repair target requires attention before retrying."
        return TaskReport(text, outcome=outcome, stop_schedule=stop_schedule)


def _is_idle_reply(text: str) -> bool:
    """Whether a reply is only the stateless idle word, allowing Markdown emphasis or a period."""
    return text.strip().strip("`*_.").strip().upper() == STATELESS_LOOP_IDLE_REPLY
