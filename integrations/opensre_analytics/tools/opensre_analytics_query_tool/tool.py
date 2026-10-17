"""Discover and call the OpenSRE Analytics app's aggregate reports.

The surface is a discovery tool plus a named-call tool over an allowlist of
reports that return counts, funnels and account listings. Reports that return
customers' own words (conversations, prompts, answers, raw event payloads,
error logs) or agent instructions are withheld: their text would enter an agent
turn that can also post and schedule, so a customer could steer it, and it
would reach people the dashboard's Tracer-only sign-in never admits. A new
analytics report stays unavailable until it is reviewed and added here.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import build_mcp_tool_listing, unavailable_response
from integrations.opensre_analytics.audience import turn_refusal
from integrations.opensre_analytics.client import (
    call_tool,
    describe_error,
    list_tools,
    tool_payload,
    unavailable_reason,
)
from integrations.opensre_analytics.config import SERVICE
from integrations.opensre_analytics.tools._sources import (
    INJECTED_PARAMS,
    analytics_available,
    analytics_params,
    config_from_params,
)

_COMPONENT = "integrations.opensre_analytics.tools.opensre_analytics_query_tool"
#: Reports the agent may read. Fail closed: anything else is refused.
AGGREGATE_REPORTS = frozenset(
    {
        "activation_funnel",
        "agent_impact",
        "ci_kpis",
        "commit_usage",
        "event_coverage",
        "fundraising_milestones",
        "github_ci_skill",
        "github_ci_sub_skills",
        "installation_funnel",
        "installations",
        "priority_funnel",
        "product_overview",
        "signup_reconciliation",
        "skill_executions",
        "skill_usage",
        "tools",
        "users",
    }
)
_WITHHELD_TEXT = (
    "{name} is not available through OpenSRE: it returns customers' conversations, "
    "answers or logs. Open it on the analytics dashboard instead."
)
_RANGE_HINT = (
    "Range-based tools take range: 1h, 8h, 24h, 3d, 7d, 14d, mtd, 30d, 90d, all, or "
    "custom:YYYY-MM-DD:YYYY-MM-DD (UTC days)."
)


@tool(
    name="list_opensre_analytics_tools",
    source=SERVICE,
    description=(
        "List the aggregate reports OpenSRE can read from the OpenSRE Analytics app (the "
        "product analytics dashboard): product overview, users, installation and "
        "activation funnels, tool usage, skill usage and more. Pass name_filter to narrow "
        "the list and include_schema=true on a narrowed list to get a tool's arguments."
    ),
    use_cases=[
        "Finding which analytics report answers a product-usage question",
        "Getting the input schema of an analytics report before calling it",
    ],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": "Space- or comma-separated terms, e.g. 'users funnel'.",
            },
            "include_schema": {
                "type": "boolean",
                "description": "Include input schemas; honored only for a small filtered list.",
            },
            "analytics_url": {"type": "string"},
            "analytics_token": {"type": "string"},
            "origin_platform": {"type": "string"},
            "origin_chat_id": {"type": "string"},
        },
        "required": [],
    },
    injected_params=INJECTED_PARAMS,
    is_available=analytics_available,
    extract_params=analytics_params,
)
def list_opensre_analytics_tools(
    name_filter: str | None = None,
    include_schema: bool = False,
    analytics_url: str | None = None,
    analytics_token: str | None = None,
    origin_platform: str | None = None,
    origin_chat_id: str | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """List the analytics app's tools in a bounded view."""
    refusal = turn_refusal(origin_platform or "", origin_chat_id or "")
    if refusal is not None:
        return {**unavailable_response(SERVICE, refusal), "tools": []}
    config = config_from_params(analytics_url, analytics_token)
    reason = unavailable_reason(config)
    if reason is not None:
        return {**unavailable_response(SERVICE, reason), "tools": []}
    try:
        tools = list_tools(config)
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_opensre_analytics_tools",
            source=SERVICE,
            component=_COMPONENT,
            method="list_tools",
        )
        return {**unavailable_response(SERVICE, describe_error(err, config)), "tools": []}
    readable = [descriptor for descriptor in tools if descriptor.get("name") in AGGREGATE_REPORTS]
    listing = build_mcp_tool_listing(
        readable,
        name_filter=(name_filter or "").strip() or None,
        include_schema=bool(include_schema),
        filter_example="users funnel overview",
    )
    return {"source": SERVICE, "available": True, "range_hint": _RANGE_HINT, **listing}


@tool(
    name="query_opensre_analytics",
    source=SERVICE,
    description=(
        "Call one aggregate OpenSRE Analytics report by name and return its JSON, e.g. "
        "product_overview {range}, users {range, sort, limit, cohort}, installation_funnel "
        "{range}, activation_funnel {range}, signup_reconciliation {range}, tools {range}. "
        f"{_RANGE_HINT} For the daily growth summary use opensre_analytics_daily_growth "
        "instead: it sums prompts and signups across accounts for you."
    ),
    use_cases=[
        "How many active users and hosted LLM calls did we have over the last 7 days",
        "Which accounts signed up most recently",
        "Where installations drop off in the activation funnel this month",
        "Which tools error most for users",
    ],
    anti_examples=["Yesterday's growth summary (use opensre_analytics_daily_growth)"],
    requires=["tool_name"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "description": "Analytics report to call."},
            "arguments": {"type": "object", "description": "The report's arguments."},
            "analytics_url": {"type": "string"},
            "analytics_token": {"type": "string"},
            "origin_platform": {"type": "string"},
            "origin_chat_id": {"type": "string"},
        },
        "required": ["tool_name"],
    },
    injected_params=INJECTED_PARAMS,
    is_available=analytics_available,
    extract_params=analytics_params,
)
def query_opensre_analytics(
    tool_name: str | None = None,
    arguments: dict[str, object] | None = None,
    analytics_url: str | None = None,
    analytics_token: str | None = None,
    origin_platform: str | None = None,
    origin_chat_id: str | None = None,
    **_kwargs: object,
) -> dict[str, Any]:
    """Call one analytics report and return its result once (not text and JSON twice)."""
    name = (tool_name or "").strip()
    args = dict(arguments or {})
    refusal = turn_refusal(origin_platform or "", origin_chat_id or "")
    if refusal is not None:
        return unavailable_response(SERVICE, refusal, tool_name=name or None, arguments=args)
    if not name:
        return unavailable_response(SERVICE, "tool_name is required.", arguments=args)
    if name not in AGGREGATE_REPORTS:
        return unavailable_response(
            SERVICE, _WITHHELD_TEXT.format(name=name), tool_name=name, arguments=args
        )
    config = config_from_params(analytics_url, analytics_token)
    reason = unavailable_reason(config)
    if reason is not None:
        return unavailable_response(SERVICE, reason, tool_name=name, arguments=args)
    try:
        result = call_tool(config, name, args)
    except Exception as err:
        report_run_error(
            err,
            tool_name="query_opensre_analytics",
            source=SERVICE,
            component=_COMPONENT,
            method="call_tool",
            extras={"analytics_tool": name},
        )
        return unavailable_response(
            SERVICE, describe_error(err, config), tool_name=name, arguments=args
        )
    if result.get("is_error"):
        message = str(result.get("text") or "").strip() or f"{name} failed."
        return unavailable_response(SERVICE, message, tool_name=name, arguments=args)
    return {
        "source": SERVICE,
        "available": True,
        "tool": name,
        "arguments": args,
        "result": tool_payload(result),
    }
