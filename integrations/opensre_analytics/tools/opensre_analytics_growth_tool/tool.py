"""Daily growth digest: signups, prompts, active accounts and installs for one UTC day."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import unavailable_response
from integrations.opensre_analytics.audience import turn_refusal
from integrations.opensre_analytics.client import unavailable_reason
from integrations.opensre_analytics.config import SERVICE
from integrations.opensre_analytics.growth import collect_growth, report_day
from integrations.opensre_analytics.render import render_growth_report
from integrations.opensre_analytics.tools._sources import (
    INJECTED_PARAMS,
    analytics_available,
    analytics_params,
    config_from_params,
)


def _parse_day(value: str | None) -> date | str:
    """The requested UTC day, yesterday by default, or an error message."""
    text = (value or "").strip()
    if not text:
        return report_day()
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return f"day must be YYYY-MM-DD, got {text!r}."
    if day > report_day():
        return "day must be a completed UTC day (yesterday or earlier)."
    return day


@tool(
    name="opensre_analytics_daily_growth",
    source=SERVICE,
    description=(
        "Daily growth digest from OpenSRE Analytics for one completed UTC day (default "
        "yesterday): new accounts, active accounts, prompts (completed agent turns), "
        "sessions, tool calls, hosted LLM calls, daily active users, new installations, "
        "account links and activation, each against the day before and a 7-day average, "
        "plus the accounts with the most prompts. OpenSRE team accounts are excluded. "
        "response_text is a ready-to-post morning report: when asked for the report, "
        "reply with it unchanged."
    ),
    use_cases=[
        "How many users did we gain yesterday",
        "How many prompts did users send yesterday",
        "Give me the morning growth report",
        "How did growth look on 2026-10-14",
    ],
    anti_examples=[
        "Post this report every morning (use schedule_analytics_growth_report)",
        "A multi-week trend or a funnel breakdown (use query_opensre_analytics)",
    ],
    outputs={
        "date": "The UTC day reported",
        "accounts": "Signups, active accounts, prompts, sessions, tool calls, top accounts",
        "installs": "New installations, account links, activation, LLM callers",
        "overview": "Hosted LLM calls and daily active users",
        "unavailable": "Sections that could not be read, with a short reason",
        "response_text": "The formatted morning report",
    },
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "day": {
                "type": "string",
                "description": "UTC day as YYYY-MM-DD; default yesterday.",
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
def opensre_analytics_daily_growth(
    day: str | None = None,
    analytics_url: str | None = None,
    analytics_token: str | None = None,
    origin_platform: str | None = None,
    origin_chat_id: str | None = None,
    **_kwargs: object,
) -> dict[str, Any]:
    """Collect one day's growth figures and render the morning report."""
    refusal = turn_refusal(origin_platform or "", origin_chat_id or "")
    if refusal is not None:
        return unavailable_response(SERVICE, refusal)
    parsed = _parse_day(day)
    if isinstance(parsed, str):
        return unavailable_response(SERVICE, parsed)
    config = config_from_params(analytics_url, analytics_token)
    reason = unavailable_reason(config)
    if reason is not None:
        return unavailable_response(SERVICE, reason)
    snapshot = collect_growth(config, day=parsed)
    figures = dataclasses.asdict(snapshot)
    return {
        "source": SERVICE,
        "available": snapshot.available,
        "date": parsed.isoformat(),
        "accounts": figures["accounts"],
        "installs": figures["installs"],
        "overview": figures["overview"],
        "unavailable": dict(snapshot.unavailable),
        "response_text": render_growth_report(snapshot),
    }
