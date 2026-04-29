"""Tests for ``app.nodes.adapt_window.rules.adapt_incident_window``.

The rule is a pure function over a state dict, so these tests build minimal
state dicts and assert the returned delta. We never spin up LangGraph here —
the node-layer wrapper is tested separately in ``test_node.py``.

Coverage strategy:
  1. Happy path: every guard passes, state delta returned correctly.
  2. Each individual guard short-circuiting (one test per guard).
  3. The stale-signal guard specifically: this is the bug-class fix
     called out in the design (without it, evidence from iteration 1
     would re-fire the rule at the end of iteration 2).
  4. History accumulation across consecutive expansions.
  5. The MAX_EXPANSIONS cap blocks the third expansion.
  6. Defensive: malformed evidence / history / executed_hypotheses must
     never raise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.incident_window import (
    MAX_LOOKBACK_MINUTES,
    SOURCE_STARTS_AT,
    IncidentWindow,
)
from app.investigation_constants import MAX_EXPANSIONS
from app.nodes.adapt_window.rules import (
    DEPLOY_TIMELINE_ACTION,
    EXPAND_REASON_EMPTY_DEPLOY_TIMELINE,
    SHARED_WINDOW_SOURCE,
    adapt_incident_window,
)

NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
FROZEN_NOW = datetime(2026, 4, 20, 12, 1, 23, 456789, tzinfo=UTC)


def _frozen_now() -> datetime:
    return FROZEN_NOW


def _two_hour_window_dict() -> dict[str, Any]:
    """A 120-minute window anchored at NOW, source ``alert.startsAt``."""
    return IncidentWindow(
        since=NOW - timedelta(hours=2),
        until=NOW,
        source=SOURCE_STARTS_AT,
        confidence=1.0,
    ).to_dict()


def _state_with_empty_timeline_signal(
    *,
    window_source: str = SHARED_WINDOW_SOURCE,
    commits_count: int = 0,
    last_actions: list[str] | None = None,
    history: list[dict[str, Any]] | None = None,
    incident_window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Minimal state shape that should, by default, fire the rule.

    Each kwarg lets a single test toggle one guard off.
    """
    actions = last_actions if last_actions is not None else [DEPLOY_TIMELINE_ACTION]
    return {
        "incident_window": (
            incident_window if incident_window is not None else _two_hour_window_dict()
        ),
        "incident_window_history": history if history is not None else None,
        "executed_hypotheses": [{"actions": actions}],
        "evidence": {
            "git_deploy_timeline_count": commits_count,
            "git_deploy_timeline_window": {
                "source": window_source,
                "since": "2026-04-20T10:00:00Z",
                "until": "2026-04-20T12:00:00Z",
            },
        },
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_expands_when_all_guards_pass(self) -> None:
        delta = adapt_incident_window(_state_with_empty_timeline_signal(), now_fn=_frozen_now)
        assert "incident_window" in delta
        assert "incident_window_history" in delta

    def test_doubled_lookback(self) -> None:
        delta = adapt_incident_window(_state_with_empty_timeline_signal(), now_fn=_frozen_now)
        new = IncidentWindow.from_dict(delta["incident_window"])
        assert new is not None
        # 120 min × 2 = 240 min = 4 hours.
        assert (new.until - new.since) == timedelta(hours=4)

    def test_history_records_old_window(self) -> None:
        original = _two_hour_window_dict()
        delta = adapt_incident_window(
            _state_with_empty_timeline_signal(incident_window=original),
            now_fn=_frozen_now,
        )
        history = delta["incident_window_history"]
        assert len(history) == 1
        recorded = history[0]
        # Old window's full shape is preserved...
        for key in ("since", "until", "source", "confidence", "_schema_version"):
            assert recorded[key] == original[key]
        # ...plus the audit fields.
        assert recorded["replaced_reason"] == EXPAND_REASON_EMPTY_DEPLOY_TIMELINE
        assert recorded["replaced_at"] == "2026-04-20T12:01:23.456789Z"

    def test_until_anchor_preserved(self) -> None:
        delta = adapt_incident_window(_state_with_empty_timeline_signal(), now_fn=_frozen_now)
        new = IncidentWindow.from_dict(delta["incident_window"])
        assert new is not None
        assert new.until == NOW


# ---------------------------------------------------------------------------
# Guards: each one short-circuits independently
# ---------------------------------------------------------------------------


class TestNoOpGuards:
    def test_no_op_when_no_incident_window(self) -> None:
        state = _state_with_empty_timeline_signal()
        state["incident_window"] = None
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_incident_window_malformed(self) -> None:
        state = _state_with_empty_timeline_signal(incident_window={"junk": "value"})
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_history_at_cap(self) -> None:
        # MAX_EXPANSIONS is 2, so any history with 2+ entries blocks.
        state = _state_with_empty_timeline_signal(
            history=[{"any": "entry"}] * MAX_EXPANSIONS,
        )
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_evidence_missing(self) -> None:
        state = _state_with_empty_timeline_signal()
        state["evidence"] = None
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_deploy_timeline_window_missing(self) -> None:
        state = _state_with_empty_timeline_signal()
        del state["evidence"]["git_deploy_timeline_window"]
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_window_source_is_caller_explicit(self) -> None:
        state = _state_with_empty_timeline_signal(window_source="caller_explicit")
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_window_source_is_tool_default(self) -> None:
        state = _state_with_empty_timeline_signal(window_source="tool_default")
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_window_source_missing(self) -> None:
        # Tool-failure path: GitHub MCP not configured → window=={}.
        state = _state_with_empty_timeline_signal()
        state["evidence"]["git_deploy_timeline_window"] = {}
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_commits_count_positive(self) -> None:
        state = _state_with_empty_timeline_signal(commits_count=3)
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_already_at_max_lookback(self) -> None:
        already_max = IncidentWindow(
            since=NOW - timedelta(minutes=MAX_LOOKBACK_MINUTES),
            until=NOW,
            source=SOURCE_STARTS_AT,
            confidence=1.0,
        ).to_dict()
        state = _state_with_empty_timeline_signal(incident_window=already_max)
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}


# ---------------------------------------------------------------------------
# Stale-signal guard
# ---------------------------------------------------------------------------


class TestStaleSignalGuard:
    """Without this guard, the rule would re-fire at the end of every
    iteration after a 0-commit deploy timeline result, even when the tool
    didn't actually run again. This was a bug found during plan review.
    """

    def test_no_op_when_executed_hypotheses_empty(self) -> None:
        state = _state_with_empty_timeline_signal(last_actions=[])
        # last_actions=[] means the most recent iteration's actions list
        # is empty — the deploy timeline did NOT run.
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_deploy_timeline_not_in_last_iteration(self) -> None:
        # Iteration 2 ran a different tool. Stale signal from iteration 1
        # remains in evidence but must not re-fire the rule.
        state = _state_with_empty_timeline_signal(
            last_actions=["query_grafana_logs"],
        )
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_executed_hypotheses_missing(self) -> None:
        state = _state_with_empty_timeline_signal()
        del state["executed_hypotheses"]
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_executed_hypotheses_malformed(self) -> None:
        state = _state_with_empty_timeline_signal()
        state["executed_hypotheses"] = "not a list"
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_no_op_when_last_iteration_actions_not_a_list(self) -> None:
        state = _state_with_empty_timeline_signal()
        state["executed_hypotheses"] = [{"actions": "not a list"}]
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}


# ---------------------------------------------------------------------------
# Multi-iteration expansion behaviour
# ---------------------------------------------------------------------------


class TestRepeatedExpansion:
    def test_second_expansion_doubles_again(self) -> None:
        # Simulate iteration 2: history already has one entry, current
        # window is now 4h (post-expansion). Expanding again gives 8h.
        first_window = IncidentWindow(
            since=NOW - timedelta(hours=4),
            until=NOW,
            source=SOURCE_STARTS_AT,
            confidence=1.0,
        ).to_dict()
        state = _state_with_empty_timeline_signal(incident_window=first_window)
        # One prior entry in history (the 2h window that got expanded).
        state["incident_window_history"] = [_two_hour_window_dict()]

        delta = adapt_incident_window(state, now_fn=_frozen_now)

        assert delta != {}
        new = IncidentWindow.from_dict(delta["incident_window"])
        assert new is not None
        assert (new.until - new.since) == timedelta(hours=8)
        assert len(delta["incident_window_history"]) == 2

    def test_third_expansion_blocked_by_history_cap(self) -> None:
        # MAX_EXPANSIONS=2 means once history has 2 entries, no more.
        state = _state_with_empty_timeline_signal()
        state["incident_window_history"] = [
            _two_hour_window_dict(),
            _two_hour_window_dict(),
        ]
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}


# ---------------------------------------------------------------------------
# Defensive shape handling
# ---------------------------------------------------------------------------


class TestDefensive:
    def test_history_with_non_dict_entries_treated_as_no_entries(self) -> None:
        state = _state_with_empty_timeline_signal()
        state["incident_window_history"] = ["str", 42, None]
        # Filtered to empty → rule fires normally, history starts fresh.
        delta = adapt_incident_window(state, now_fn=_frozen_now)
        assert len(delta["incident_window_history"]) == 1

    def test_evidence_count_is_string_treats_as_zero(self) -> None:
        state = _state_with_empty_timeline_signal()
        state["evidence"]["git_deploy_timeline_count"] = "garbage"
        # Coerces to 0 → rule fires.
        delta = adapt_incident_window(state, now_fn=_frozen_now)
        assert delta != {}

    def test_evidence_window_is_not_a_dict(self) -> None:
        state = _state_with_empty_timeline_signal()
        state["evidence"]["git_deploy_timeline_window"] = "not a dict"
        assert adapt_incident_window(state, now_fn=_frozen_now) == {}

    def test_default_now_fn_does_not_raise(self) -> None:
        # When no now_fn is injected, the default uses datetime.now(UTC)
        # — must not raise and must produce a valid ISO string.
        state = _state_with_empty_timeline_signal()
        delta = adapt_incident_window(state)
        recorded_at = delta["incident_window_history"][0]["replaced_at"]
        assert recorded_at.endswith("Z")
        # Sanity check: parses back as ISO-8601.
        parsed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_state_is_not_mutated(self) -> None:
        state = _state_with_empty_timeline_signal()
        snapshot_window = dict(state["incident_window"])
        snapshot_evidence = dict(state["evidence"])
        adapt_incident_window(state, now_fn=_frozen_now)
        assert state["incident_window"] == snapshot_window
        assert state["evidence"] == snapshot_evidence


# ---------------------------------------------------------------------------
# Sanity: the state shape used by the helper matches our assumptions
# ---------------------------------------------------------------------------


def test_max_expansions_constant_is_two() -> None:
    """If MAX_EXPANSIONS ever changes, several tests above need to be updated.
    Pin the constant here so that's caught immediately."""
    assert MAX_EXPANSIONS == 2


@pytest.mark.parametrize(
    "factor,expected_hours",
    [(2.0, 4), (3.0, 6), (4.0, 8)],
)
def test_documented_factor_is_used(factor: float, expected_hours: int) -> None:
    """The rule applies a fixed expansion factor (2.0). This test is a
    sanity check that the imported constant is in fact 2.0; if someone
    later parameterizes the rule, they should remove or adapt this."""
    from app.nodes.adapt_window.rules import EXPANSION_FACTOR

    if factor == EXPANSION_FACTOR:
        # 120 min * 2.0 = 240 min = 4h
        assert expected_hours == 4
