"""Pure adaptive-window rule logic.

This module is intentionally free of LangChain / LangGraph imports so the
rules can be tested in isolation against plain dicts. The node entry point
in ``app.nodes.adapt_window.node`` wraps these functions in the ``@traceable``
decorator.

Today there is exactly one rule: **expand the window when the deploy
timeline came back empty for a shared-window query**. The rule is
deliberately conservative — it only widens when:

- the GitDeployTimelineTool actually ran in the most recent investigation
  iteration (defended by reading ``state.executed_hypotheses[-1].actions``);
- its returned window's ``source`` was ``"shared_incident_window"``
  (caller-explicit windows are NEVER overridden);
- its ``commits_count`` was zero;
- the existing ``state.incident_window_history`` has fewer than
  ``MAX_EXPANSIONS`` entries (worst-case bound on expansion);
- expanding would actually widen the window — i.e. we are not already at
  ``MAX_LOOKBACK_MINUTES``.

When all guards pass, the rule emits a state delta containing the new
window plus the OLD window appended to history with ``replaced_at`` and
``replaced_reason``. When any guard fails the rule returns an empty dict
and nothing in state changes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.incident_window import IncidentWindow
from app.investigation_constants import MAX_EXPANSIONS

DEPLOY_TIMELINE_ACTION = "get_git_deploy_timeline"
SHARED_WINDOW_SOURCE = "shared_incident_window"
EXPANSION_FACTOR = 2.0
EXPAND_REASON_EMPTY_DEPLOY_TIMELINE = "expanded:empty_deploy_timeline"


def _strictly_wider(new: IncidentWindow, old: IncidentWindow) -> bool:
    """Return True when ``new`` covers MORE time than ``old``.

    Used to detect "expanded() returned the same width" — happens when the
    current window is already at MAX_LOOKBACK_MINUTES. A no-op expansion
    must not be recorded in history because nothing actually changed.
    """
    return (new.until - new.since) > (old.until - old.since)


def _last_iteration_actions(state: dict[str, Any]) -> list[str]:
    """Return the action names executed in the most recent investigation
    loop, or an empty list if there are none / the shape is malformed.

    Reads ``state.executed_hypotheses[-1].actions``. Each entry in
    ``executed_hypotheses`` is a dict with an ``actions: list[str]``
    sub-field. Defensive: any wrong shape downgrades to an empty list,
    which the caller treats as "no recent tool activity, no-op".
    """
    history = state.get("executed_hypotheses")
    if not isinstance(history, list) or not history:
        return []
    last = history[-1]
    if not isinstance(last, dict):
        return []
    actions = last.get("actions")
    if not isinstance(actions, list):
        return []
    return [item for item in actions if isinstance(item, str)]


def _now_iso(now_fn: Callable[[], datetime]) -> str:
    """Render ``now_fn()`` as ISO-8601 with the trailing ``Z`` shorthand."""
    current = now_fn()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def adapt_incident_window(
    state: dict[str, Any],
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Decide whether to widen ``state.incident_window`` for the next
    investigation iteration. Returns a state-delta dict (the keys
    LangGraph should merge into state) or an empty dict for "no change".

    The function is pure: it never raises, never mutates ``state``, never
    performs I/O. ``now_fn`` is injected so tests can pin the
    ``replaced_at`` timestamp deterministically; production callers leave
    the default.

    See module docstring for the full guard chain.
    """
    # 1. Window must be present and well-formed.
    current_dict = state.get("incident_window")
    if not isinstance(current_dict, dict):
        return {}
    current = IncidentWindow.from_dict(current_dict)
    if current is None:
        return {}

    # 2. History must be within cap. Treat malformed history as "no entries"
    # so a single corrupted record cannot strand adaptation forever.
    raw_history = state.get("incident_window_history")
    history: list[dict[str, Any]] = (
        [entry for entry in raw_history if isinstance(entry, dict)]
        if isinstance(raw_history, list)
        else []
    )
    if len(history) >= MAX_EXPANSIONS:
        return {}

    # 3. Stale-signal guard: the deploy timeline tool must have run in the
    # most recent iteration. Without this check, a 0-commit result from
    # iteration 1 would re-fire the rule at the end of iteration 2 even
    # when iteration 2 ran completely different tools.
    if DEPLOY_TIMELINE_ACTION not in _last_iteration_actions(state):
        return {}

    # 4. Read the tool's evidence. The mapper in
    # ``investigate.processing.post_process._map_git_deploy_timeline``
    # publishes the full ``window`` dict under this key.
    evidence = state.get("evidence")
    if not isinstance(evidence, dict):
        return {}
    window_info = evidence.get("git_deploy_timeline_window")
    if not isinstance(window_info, dict):
        return {}

    # 5. Only expand when the tool used the shared window. caller_explicit
    # / tool_default / unset (== {}) all fall through.
    if window_info.get("source") != SHARED_WINDOW_SOURCE:
        return {}

    # 6. Empty result is the trigger. A None / missing count is treated as
    # zero so a partial response cannot misfire the rule the other way.
    try:
        commits_count = int(evidence.get("git_deploy_timeline_count") or 0)
    except (TypeError, ValueError):
        commits_count = 0
    if commits_count > 0:
        return {}

    # 7. Compute the expansion. If we are already at MAX_LOOKBACK_MINUTES,
    # ``expanded`` returns a window of the same width — nothing to record.
    new_window = current.expanded(factor=EXPANSION_FACTOR)
    if not _strictly_wider(new_window, current):
        return {}

    # 8. Build the audit entry for the OLD window.
    old_entry: dict[str, Any] = {
        **current_dict,
        "replaced_at": _now_iso(now_fn),
        "replaced_reason": EXPAND_REASON_EMPTY_DEPLOY_TIMELINE,
    }

    return {
        "incident_window": new_window.to_dict(),
        "incident_window_history": [*history, old_entry],
    }


__all__ = [
    "DEPLOY_TIMELINE_ACTION",
    "EXPAND_REASON_EMPTY_DEPLOY_TIMELINE",
    "EXPANSION_FACTOR",
    "SHARED_WINDOW_SOURCE",
    "adapt_incident_window",
]
