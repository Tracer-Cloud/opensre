"""The vendor metric contract: registration is checked, and covers classification.

An integration that registers everything the contract offers must be treated
like a first-class source by the whole metric path — the draft fence, the
discovery budget, and the "did a query run" check. Registering only a draft
left a vendor silently second-class.
"""

from __future__ import annotations

import pytest

import infrastructure.harness_providers as harness_providers
from core.agent_harness.turns.gather_discovery_budget import (
    is_gather_discovery_call,
    is_live_metric_query_call,
)

_VENDOR = "newrelic"
_DRAFT = "```sql\nSELECT 1\n```"


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    harness_providers.clear_metric_query_drafts()
    yield
    harness_providers.clear_metric_query_drafts()


def test_a_registered_vendor_query_tool_counts_as_a_live_metric_query() -> None:
    """Classification must be registrable, not hard-coded to two vendors."""
    # Arrange
    harness_providers.register_metric_query_tools(_VENDOR, ("call_newrelic_nrql",))

    # Act / Assert
    assert is_live_metric_query_call("call_newrelic_nrql", {"query": "SELECT 1"}) is True
    from core.agent_harness.turns.gather_discovery_budget import is_mcp_metric_target

    assert is_mcp_metric_target("call_newrelic_nrql") is True


def test_a_registered_vendor_discovery_target_spends_the_discovery_budget() -> None:
    """Discovery must be registrable so a new vendor's probes are budgeted."""
    # Arrange
    harness_providers.register_discovery_targets(_VENDOR, ("describe-events",))

    # Act / Assert
    assert is_gather_discovery_call("call_newrelic_tool", {"tool_name": "describe-events"}) is True


def test_an_unregistered_tool_is_not_a_metric_query() -> None:
    """The registry is a closed set: absence must not be guessed as a fetch."""
    assert is_live_metric_query_call("call_newrelic_nrql", {"query": "SELECT 1"}) is False


def test_mcp_metric_target_does_not_guess_from_query_prefix() -> None:
    """``query-*`` alone is not a metric tool — vendors must register names."""
    from core.agent_harness.turns.gather_discovery_budget import is_mcp_metric_target

    assert is_mcp_metric_target("query-made-up-tool") is False
    assert is_mcp_metric_target("execute-sql") is False  # cleared by fixture


def test_registering_an_empty_service_id_is_a_wiring_error() -> None:
    """A blank id silently registered nothing and surfaced as a generic fence."""
    with pytest.raises(ValueError):
        harness_providers.register_metric_query_draft("", count_draft=_DRAFT)


def test_registering_an_empty_draft_is_a_wiring_error() -> None:
    with pytest.raises(ValueError):
        harness_providers.register_metric_query_draft(_VENDOR, count_draft="   ")


def test_draft_precedence_follows_registration_priority_not_caller_order() -> None:
    """Two connected analytics sources must resolve deterministically.

    First-match-on-caller-tuple made the winner depend on how an unrelated
    tuple was assembled.
    """
    # Arrange
    harness_providers.register_metric_query_draft("grafana", count_draft="GRAFANA", priority=10)
    harness_providers.register_metric_query_draft("posthog_mcp", count_draft="POSTHOG", priority=1)

    # Act / Assert — same set, either caller order, same winner.
    assert harness_providers.metric_query_draft_for(("grafana", "posthog_mcp")) == "POSTHOG"
    assert harness_providers.metric_query_draft_for(("posthog_mcp", "grafana")) == "POSTHOG"
