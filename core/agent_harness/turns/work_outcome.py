"""Last-work-tool outcome for the ReAct goal gate.

A failed ``shell_run`` / curl still returns as a tool observation (``ok:
false``, nonzero ``exit_code``), so the loop would otherwise accept a
conclusion. The host rejects stop until a later work tool succeeds. Skills
cannot override this.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.agent_harness.task_plan.evidence import is_plan_work_name, result_counts_as_work
from core.events import RuntimeEvent, RuntimeEventCallback, ToolExecutionEndEvent


@dataclass(frozen=True, slots=True)
class ExecutedToolOutcome:
    """One finished tool call as the goal reviewer sees it."""

    name: str
    arguments: dict[str, Any]
    is_error: bool
    details: Any


def tap_executed_tool_outcomes(
    inner: RuntimeEventCallback | None,
    outcomes: list[ExecutedToolOutcome],
) -> RuntimeEventCallback:
    """Wrap ``inner`` to record each executed tool's payload into ``outcomes``."""

    def _callback(event: RuntimeEvent) -> None:
        if isinstance(event, ToolExecutionEndEvent):
            outcomes.append(
                ExecutedToolOutcome(
                    name=event.tool_name,
                    arguments=dict(event.args or {}),
                    is_error=event.is_error,
                    details=event.result,
                )
            )
        if inner is not None:
            inner(event)

    return _callback


def last_work_ok(outcomes: Sequence[ExecutedToolOutcome]) -> bool | None:
    """Whether the last non-bookkeeping tool succeeded.

    ``None`` when no work tool ran, ``False`` when it failed, ``True`` when it
    succeeded. Bookkeeping cannot hide a failed curl.
    """
    last: bool | None = None
    for outcome in outcomes:
        if not is_plan_work_name(outcome.name, outcome.arguments):
            continue
        details = outcome.details if isinstance(outcome.details, Mapping) else None
        last = result_counts_as_work(is_error=outcome.is_error, details=details)
    return last


def last_work_tool_failed(outcomes: Sequence[ExecutedToolOutcome]) -> bool:
    """True when the last non-bookkeeping tool did not count as successful work."""
    return last_work_ok(outcomes) is False


_CLASSIFIED_WORK_STATUSES = frozenset({"succeeded", "noop", "blocked", "failed"})


def last_work_classified(outcomes: Sequence[ExecutedToolOutcome]) -> bool:
    """True when the last work tool already published a finished ``work_outcome``.

    A failed curl has no outcome and must keep the turn open. A repair that
    classified the target (blocked, failed, noop, or succeeded) has finished,
    so the turn may report that result instead of retrying it.
    """
    for outcome in reversed(outcomes):
        if not is_plan_work_name(outcome.name, outcome.arguments):
            continue
        details = outcome.details if isinstance(outcome.details, Mapping) else None
        raw = details.get("work_outcome") if isinstance(details, Mapping) else None
        if not isinstance(raw, Mapping):
            return False
        return str(raw.get("status") or "") in _CLASSIFIED_WORK_STATUSES
    return False


_REVIEW_PAYLOAD_CHARS = 400


def format_outcomes_for_review(outcomes: Sequence[ExecutedToolOutcome]) -> str:
    """Compact tool payloads for the optional LLM goal review."""
    if not outcomes:
        return "(none)"
    parts: list[str] = []
    for outcome in outcomes:
        payload = outcome.details
        text = repr(payload)
        if len(text) > _REVIEW_PAYLOAD_CHARS:
            text = text[: _REVIEW_PAYLOAD_CHARS - 3] + "..."
        parts.append(f"{outcome.name}: {text}")
    return "; ".join(parts)


__all__ = [
    "ExecutedToolOutcome",
    "format_outcomes_for_review",
    "last_work_classified",
    "last_work_ok",
    "last_work_tool_failed",
    "tap_executed_tool_outcomes",
]
