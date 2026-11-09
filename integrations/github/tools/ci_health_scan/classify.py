"""Classify one head commit's GraphQL ``statusCheckRollup`` into failing checks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from integrations.github.tools.ci_health_scan.models import CheckSummary, FailingCheck

#: Check-run conclusions that mean the check failed.
FAILED_CHECK_CONCLUSIONS = frozenset({"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"})
#: Commit-status states that mean the status failed.
FAILED_STATUS_STATES = frozenset({"FAILURE", "ERROR"})
#: Cancelled is reported beside the failures, never counted as one: it is
#: usually a superseded run, not a broken build.
CANCELLED_CONCLUSION = "CANCELLED"
#: ``contexts(first: N)`` page size the query asks for; more than this is truncated.
CONTEXTS_PAGE_SIZE = 100

_EMPTY = CheckSummary(rollup_state="", failing=(), cancelled=(), truncated=False)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def classify_rollup(rollup: Any, extra_nodes: Sequence[Any] = ()) -> CheckSummary:
    """Reduce a ``statusCheckRollup`` node to the checks that failed on it.

    Returns an empty summary when the commit has no rollup at all (no CI ran).
    Check-runs are judged by ``conclusion``, status contexts by ``state``.
    ``extra_nodes`` are context nodes fetched from later pages; ``truncated``
    is true while ``totalCount`` still exceeds every node seen.
    """
    if not isinstance(rollup, dict):
        return _EMPTY
    contexts = rollup.get("contexts")
    first_page = contexts.get("nodes") if isinstance(contexts, dict) else None
    if not isinstance(first_page, list):
        return CheckSummary(
            rollup_state=_text(rollup.get("state")), failing=(), cancelled=(), truncated=False
        )
    nodes = [*first_page, *extra_nodes]
    # Two workflow runs on one commit (e.g. push and pull_request events) each
    # contribute a check with the same name; the model needs each name once.
    failing: dict[str, FailingCheck] = {}
    cancelled: dict[str, None] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if "conclusion" in node:
            conclusion = _text(node.get("conclusion")).upper()
            name = _text(node.get("name")) or "Unnamed check"
            if conclusion in FAILED_CHECK_CONCLUSIONS:
                url = _text(node.get("detailsUrl")) or _text(node.get("permalink"))
                failing.setdefault(name, FailingCheck(name=name, state=conclusion, url=url))
            elif conclusion == CANCELLED_CONCLUSION:
                cancelled.setdefault(name)
            continue
        state = _text(node.get("state")).upper()
        if state in FAILED_STATUS_STATES:
            name = _text(node.get("context")) or "Unnamed status"
            failing.setdefault(
                name, FailingCheck(name=name, state=state, url=_text(node.get("targetUrl")))
            )
    total = contexts.get("totalCount") if isinstance(contexts, dict) else None
    truncated = isinstance(total, int) and total > len(nodes)
    return CheckSummary(
        rollup_state=_text(rollup.get("state")),
        failing=tuple(failing.values()),
        cancelled=tuple(cancelled),
        truncated=truncated,
    )


__all__ = [
    "CANCELLED_CONCLUSION",
    "CONTEXTS_PAGE_SIZE",
    "FAILED_CHECK_CONCLUSIONS",
    "FAILED_STATUS_STATES",
    "classify_rollup",
]
