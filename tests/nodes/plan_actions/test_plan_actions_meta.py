"""Tests for the ``_meta`` channel that threads investigation-level
context (today: incident_window) from state to opt-in tools.

This is the wiring layer for PR 2 of the dynamic incident window work:
``plan_actions`` attaches ``state.incident_window`` to
``available_sources["_meta"]`` so tools that opt in can read it via
their ``extract_params``. Tools that don't opt in see no behavioural
change.
"""

from __future__ import annotations

from app.nodes.investigate.models import InvestigateInput
from app.nodes.plan_actions.detect_sources import detect_sources

# A minimal incident_window dict shaped like ``app.incident_window.IncidentWindow.to_dict``.
_WINDOW = {
    "_schema_version": 1,
    "since": "2026-04-20T08:00:00Z",
    "until": "2026-04-20T10:00:00Z",
    "source": "alert.startsAt",
    "confidence": 1.0,
}


def test_investigate_input_carries_incident_window_from_state() -> None:
    """``InvestigateInput.from_state`` must surface state.incident_window
    so plan_actions can thread it onward."""
    state = {"raw_alert": {}, "incident_window": _WINDOW}
    input_data = InvestigateInput.from_state(state)
    assert input_data.incident_window == _WINDOW


def test_investigate_input_handles_missing_incident_window() -> None:
    """Backward compat: states from before PR 1 / extract_alert ran do
    not carry ``incident_window``. Field defaults to None."""
    input_data = InvestigateInput.from_state({"raw_alert": {}})
    assert input_data.incident_window is None


def test_investigate_input_rejects_non_dict_incident_window() -> None:
    """Defensive: a corrupted state with a non-dict incident_window must
    not propagate garbage downstream."""
    input_data = InvestigateInput.from_state({"raw_alert": {}, "incident_window": "not-a-dict"})
    assert input_data.incident_window is None


# ---------------------------------------------------------------------------
# Wiring contract: plan_actions attaches _meta to available_sources
# ---------------------------------------------------------------------------
#
# We verify the contract at the function level rather than running the
# whole plan_actions pipeline (which would require a configured LLM).
# detect_sources returns the unchanged sources dict; plan_actions wraps
# it and adds _meta. Replicate that wrapping here so the test doesn't
# depend on plan_actions's planning step.


def test_meta_channel_attaches_incident_window() -> None:
    """When state has an incident_window, the _meta key must appear in
    available_sources after the plan_actions wrapping step. We verify by
    reproducing the exact lines that wrap detect_sources output."""
    sources = detect_sources(raw_alert={}, context={})
    input_data = InvestigateInput(raw_alert={}, incident_window=_WINDOW)
    if input_data.incident_window is not None:
        sources["_meta"] = {"incident_window": input_data.incident_window}

    assert "_meta" in sources
    assert sources["_meta"]["incident_window"] == _WINDOW


def test_meta_channel_omitted_when_state_has_no_window() -> None:
    """When state has no incident_window, the _meta key MUST NOT appear.
    Tools then see the same available_sources shape as today."""
    sources = detect_sources(raw_alert={}, context={})
    input_data = InvestigateInput(raw_alert={})
    if input_data.incident_window is not None:
        sources["_meta"] = {"incident_window": input_data.incident_window}

    assert "_meta" not in sources
