"""Gather discovery budget — block MCP schema thrash.

Live symptom (session e4b6f56b…): one Windows-users metric ask spent the
gather turn on ``list_posthog_tools`` + many ``call_posthog_tool`` exec
``search`` / ``info`` / ``schema`` / ``read-data-schema`` calls and never
ran a count query. Circuit breaker only trips on connectivity errors, so
successful discovery loops forever.

This guard caps discovery-style MCP bridge calls (``list_*_tools`` /
``call_*_tool``) per gather turn and dedupes exact fingerprints without
``json.dumps``.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.turns.gather_discovery_budget import (
    DEFAULT_MAX_DISCOVERY_CALLS,
    is_gather_discovery_call,
    is_live_metric_query_call,
    is_mcp_exec_bridge,
    is_mcp_list_tools,
    with_gather_discovery_budget,
)
from core.llm.types import ToolCall
from core.tool.contracts import AgentTool, AgentToolContext
from core.tool.execution import ToolExecutionRequest, ToolExecutionResult


def _execute_tool(_arguments: dict[str, Any], _context: AgentToolContext) -> None:
    """Provide the execution contract for request metadata used by hook tests."""


def _request(
    tool: str,
    arguments: dict,
    *,
    source: str = "posthog_mcp",
) -> ToolExecutionRequest:
    runtime_tool = AgentTool(
        name=tool,
        description="Test tool",
        input_schema={"type": "object"},
        execute=_execute_tool,
        source=source,
    )
    return ToolExecutionRequest(
        tool_call=ToolCall(id="tc", name=tool, input=arguments),
        tool=runtime_tool,
        arguments=arguments,
        source=source,
        resolved_integrations={},
    )


def _ok() -> ToolExecutionResult:
    return ToolExecutionResult(content='{"ok": true}', is_error=False)


def test_discovery_burst_is_capped() -> None:
    """More than the budget of discovery calls in one gather turn is blocked."""
    hooks = with_gather_discovery_budget()
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None

    # First list + three schema probes are allowed (within default budget).
    allowed = [
        _request("list_posthog_tools", {"name_filter": "exec", "include_schema": True}),
        _request(
            "call_posthog_tool",
            {
                "name": "exec",
                "arguments": {
                    "command": "search read-data-schema",
                    "context": "locate schema tool",
                },
            },
        ),
        _request(
            "call_posthog_tool",
            {
                "name": "exec",
                "arguments": {
                    "command": "info query-trends",
                    "context": "inspect trends",
                },
            },
        ),
        _request(
            "call_posthog_tool",
            {
                "name": "exec",
                "arguments": {
                    "command": "schema query-trends series",
                    "context": "series schema",
                },
            },
        ),
    ]
    for req in allowed:
        assert hooks.before_tool_call(req) is None
        hooks.after_tool_call(req, _ok())

    # Next discovery call after the budget must be blocked.
    overflow = _request(
        "call_posthog_tool",
        {
            "name": "exec",
            "arguments": {
                "command": "schema query-trends dateRange",
                "context": "date range schema",
            },
        },
    )
    decision = hooks.before_tool_call(overflow)
    assert decision is not None
    assert decision.blocked
    assert "discovery" in decision.reason.lower()
    assert str(DEFAULT_MAX_DISCOVERY_CALLS) in decision.reason


def test_exact_duplicate_discovery_blocked_even_under_budget() -> None:
    hooks = with_gather_discovery_budget(max_discovery_calls=8)
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None
    req = _request(
        "call_posthog_tool",
        {
            "name": "exec",
            "arguments": {"command": "search query-trends", "context": "a"},
        },
    )
    assert hooks.before_tool_call(req) is None
    hooks.after_tool_call(req, _ok())
    # Same command, different context prose — still a duplicate discovery.
    dup = _request(
        "call_posthog_tool",
        {
            "name": "exec",
            "arguments": {"command": "search query-trends", "context": "b"},
        },
    )
    decision = hooks.before_tool_call(dup)
    assert decision is not None
    assert decision.blocked
    assert "already" in decision.reason.lower()


def test_real_query_call_still_allowed_after_discovery_budget() -> None:
    hooks = with_gather_discovery_budget(max_discovery_calls=1)
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None
    list_req = _request("list_posthog_tools", {"name_filter": "exec"})
    assert hooks.before_tool_call(list_req) is None
    hooks.after_tool_call(list_req, _ok())

    query = _request(
        "call_posthog_tool",
        {
            "name": "exec",
            "arguments": {
                "command": (
                    'call query-trends {"dateRange":{"date_from":"-7d"},'
                    '"series":[{"event":"cli_invoked","math":"dau"}]}'
                ),
                "context": "count Windows users",
            },
        },
    )
    assert hooks.before_tool_call(query) is None


def test_posthog_tool_name_schema_probe_is_discovery() -> None:
    """Live PostHog uses tool_name=…, not exec command strings."""
    assert is_gather_discovery_call(
        "call_posthog_tool",
        {"tool_name": "read-data-schema", "arguments": {"entity": "events"}},
    )
    assert is_gather_discovery_call(
        "call_posthog_tool",
        {"tool_name": "event-definitions", "arguments": {}},
    )
    assert not is_gather_discovery_call(
        "call_posthog_tool",
        {
            "tool_name": "execute-sql",
            "arguments": {"query": "SELECT 1"},
        },
    )
    assert not is_gather_discovery_call(
        "call_posthog_tool",
        {"tool_name": "query-trends", "arguments": {"dateRange": {"date_from": "-7d"}}},
    )


def test_posthog_tool_name_discovery_burst_is_capped() -> None:
    hooks = with_gather_discovery_budget(max_discovery_calls=2)
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None
    for entity in ("events", "persons"):
        req = _request(
            "call_posthog_tool",
            {"tool_name": "read-data-schema", "arguments": {"entity": entity}},
        )
        assert hooks.before_tool_call(req) is None
        hooks.after_tool_call(req, _ok())
    overflow = _request(
        "call_posthog_tool",
        {"tool_name": "event-definitions", "arguments": {}},
    )
    decision = hooks.before_tool_call(overflow)
    assert decision is not None and decision.blocked
    # Metric query still allowed after discovery budget.
    query = _request(
        "call_posthog_tool",
        {
            "tool_name": "execute-sql",
            "arguments": {"query": "SELECT uniqExact(distinct_id) FROM events"},
        },
    )
    assert hooks.before_tool_call(query) is None


def test_failed_discovery_still_counts_toward_budget() -> None:
    hooks = with_gather_discovery_budget(max_discovery_calls=1)
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None
    req = _request(
        "call_posthog_tool",
        {"tool_name": "read-data-schema", "arguments": {"entity": "events"}},
    )
    assert hooks.before_tool_call(req) is None
    hooks.after_tool_call(
        req,
        ToolExecutionResult(
            content="Entity events does not exist in the taxonomy.",
            is_error=True,
        ),
    )
    overflow = _request(
        "call_posthog_tool",
        {"tool_name": "event-definitions", "arguments": {}},
    )
    decision = hooks.before_tool_call(overflow)
    assert decision is not None and decision.blocked


def test_mcp_bridge_naming_matches_any_vendor_not_just_posthog() -> None:
    assert is_mcp_list_tools("list_acme_tools")
    assert is_mcp_exec_bridge("call_acme_tool")
    assert not is_mcp_list_tools("list_tools")
    assert not is_mcp_exec_bridge("call_tool")
    assert not is_mcp_list_tools("query_grafana_logs")


def test_non_posthog_mcp_bridge_discovery_is_capped() -> None:
    hooks = with_gather_discovery_budget(max_discovery_calls=1)
    assert hooks.before_tool_call is not None
    assert hooks.after_tool_call is not None
    first = _request(
        "list_acme_tools",
        {"name_filter": "exec"},
        source="acme_mcp",
    )
    assert hooks.before_tool_call(first) is None
    hooks.after_tool_call(first, _ok())
    overflow = _request(
        "call_acme_tool",
        {"name": "exec", "arguments": {"command": "schema query-metrics"}},
        source="acme_mcp",
    )
    decision = hooks.before_tool_call(overflow)
    assert decision is not None and decision.blocked


def test_data_fetching_targets_are_not_discovery() -> None:
    """A bridge target that fetches evidence must not spend the discovery budget.

    Only ``execute-sql`` and ``query-*`` were treated as real queries, so every
    other vendor's fetch — Sentry ``issue_get`` or X ``search_tweets`` — counted
    as discovery. That exhausts the allowance
    and lets the goal reviewer reject a gather that already fetched what was
    asked for.
    """
    # Arrange / Act / Assert.
    for target in ("issue_get", "search_tweets", "conversations_get", "get_organization_events"):
        assert not is_gather_discovery_call(
            "call_posthog_tool", {"tool_name": target, "arguments": {}}
        ), f"{target} is a data fetch, not discovery"


def test_known_discovery_targets_still_count() -> None:
    """The budget must still catch the schema loop it exists for."""
    # Arrange / Act / Assert.
    for target in ("read-data-schema", "skill-get"):
        assert is_gather_discovery_call(
            "call_posthog_tool", {"tool_name": target, "arguments": {}}
        ), f"{target} is discovery"


def test_live_metric_query_call_is_narrower_than_non_discovery() -> None:
    """Metric floor must not treat every non-discovery fetch as a count query."""
    assert is_live_metric_query_call(
        "call_posthog_tool",
        {"tool_name": "execute-sql", "arguments": {"query": "SELECT 1"}},
    )
    assert is_live_metric_query_call(
        "call_posthog_tool",
        {"tool_name": "query-trends", "arguments": {}},
    )
    assert is_live_metric_query_call("query_grafana_metrics", {"expr": "up"})
    assert is_live_metric_query_call("execute-sql", {})
    # Exact membership only — no substring / dual-spelling guesses.
    assert not is_live_metric_query_call("posthog_mcp execute-sql", {})
    assert not is_live_metric_query_call("execute_sql", {})
    # Data fetches that are not metric queries.
    for target in ("issue_get", "search_tweets", "conversations_get"):
        assert not is_live_metric_query_call(
            "call_posthog_tool", {"tool_name": target, "arguments": {}}
        ), f"{target} must not count as a live metric query"
    assert not is_live_metric_query_call("query_grafana_alert_rules", {})
