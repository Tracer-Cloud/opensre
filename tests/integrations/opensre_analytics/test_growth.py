"""Daily growth figures: sums over accounts, and outages that must never read as zero.

The report is posted to a product channel every morning, so a source outage
reported as "0 prompts" would be read as a collapse in usage.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from integrations.opensre_analytics.client import AnalyticsToolError
from integrations.opensre_analytics.config import OpenSREAnalyticsConfig
from integrations.opensre_analytics.growth import DailyCount, TopAccount, collect_growth
from integrations.opensre_analytics.render import render_growth_report

DAY = date(2026, 10, 16)
CONFIG = OpenSREAnalyticsConfig(auth_token="analytics-token")
TODAY = "custom:2026-10-16:2026-10-16"
YESTERDAY = "custom:2026-10-15:2026-10-15"
WEEK = "custom:2026-10-09:2026-10-16"


def _account(
    user_id: str,
    registered: str,
    *,
    turns: int = 0,
    sessions: int = 0,
    tool_calls: int = 0,
    cohort: str = "external",
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "userId": user_id,
        "name": name,
        "registeredAt": f"{registered}T10:00:00.000Z",
        "turns": turns,
        "sessions": sessions,
        "toolCalls": tool_calls,
        "cohort": cohort,
    }


def _users(rows: list[dict[str, Any]], *, matched: int | None = None) -> dict[str, Any]:
    return {
        "available": True,
        "sources": {"directory": {"available": True}, "activity": {"available": True}},
        "totalUsers": matched or len(rows),
        "matchedUsers": matched or len(rows),
        "users": rows,
    }


class _FakeAnalytics:
    """Answers analytics tool calls from canned payloads keyed by tool, range and sort."""

    def __init__(self, responses: dict[tuple[str, str, str], Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, str]] = []

    def __call__(
        self, _config: OpenSREAnalyticsConfig, tool: str, arguments: dict[str, object]
    ) -> dict[str, Any]:
        key = (tool, str(arguments.get("range")), str(arguments.get("sort", "")))
        self.calls.append(key)
        response = self.responses[key]
        if isinstance(response, Exception):
            raise response
        return dict(response)


def _installs() -> dict[str, Any]:
    return {
        "available": True,
        "daily": [
            {"date": "2026-10-16", "installs": 7, "authenticated": 3, "activated": 1},
            {"date": "2026-10-15", "installs": 4, "authenticated": 1, "activated": 0},
        ],
        "dailyLlmCallers": [{"date": "2026-10-16", "users": 5, "calls": 40}],
    }


def _overview() -> dict[str, Any]:
    return {
        "available": True,
        "daily": [
            {"date": "2026-10-16", "activeUsers": 20, "hostedLlmCalls": 300},
            {"date": "2026-10-15", "activeUsers": 18, "hostedLlmCalls": 250},
        ],
    }


def test_sums_prompts_and_signups_over_accounts_excluding_the_team() -> None:
    # Arrange: a complete account listing, so one read serves signups and usage.
    analytics = _FakeAnalytics(
        {
            ("users", TODAY, "registered"): _users(
                [
                    _account("ada", "2026-10-16", turns=5, sessions=1, tool_calls=7, name="Ada"),
                    _account("team", "2026-10-16", turns=100, sessions=9, cohort="team"),
                    _account("bo", "2026-10-15", turns=3, sessions=2, tool_calls=1),
                    _account("cy", "2026-10-13", cohort="unknown"),
                    _account("old", "2026-10-01"),
                ]
            ),
            ("users", YESTERDAY, "calls"): _users(
                [
                    _account("team", "2026-10-16", turns=50, cohort="team"),
                    _account("ada", "2026-10-16", turns=2, sessions=1),
                ]
            ),
            ("installation_funnel", WEEK, ""): _installs(),
            ("product_overview", WEEK, ""): _overview(),
        }
    )

    # Act
    snapshot = collect_growth(CONFIG, day=DAY, call=analytics)

    # Assert
    accounts = snapshot.accounts
    assert accounts is not None
    assert accounts.prompts == DailyCount(8, 2)
    assert accounts.active_accounts == DailyCount(2, 1)
    assert accounts.new_accounts == DailyCount(1, 1, 2 / 7)
    assert accounts.top_accounts == (TopAccount("Ada", 5), TopAccount("Unnamed account", 3))
    assert not accounts.partial
    assert snapshot.installs is not None
    assert snapshot.installs.new_installs == DailyCount(7, 4, 4 / 7)
    assert snapshot.unavailable == {}
    # A complete listing needs no second, calls-sorted read of the day.
    assert ("users", TODAY, "calls") not in analytics.calls


def test_a_source_outage_is_reported_unavailable_never_as_zero() -> None:
    # Arrange: the account directory answers but activity is down; the other
    # sources fail outright, one with detail that must not reach the channel.
    accounts_without_activity = _users([_account("ada", "2026-10-16")])
    accounts_without_activity["sources"]["activity"] = {"available": False, "error": "down"}
    analytics = _FakeAnalytics(
        {
            ("users", TODAY, "registered"): accounts_without_activity,
            ("users", YESTERDAY, "calls"): _users([]),
            ("installation_funnel", WEEK, ""): httpx.ConnectError("connection refused"),
            ("product_overview", WEEK, ""): AnalyticsToolError("password=hunter2 rejected"),
        }
    )

    # Act
    snapshot = collect_growth(CONFIG, day=DAY, call=analytics)
    report = render_growth_report(snapshot)

    # Assert
    assert not snapshot.available
    assert set(snapshot.unavailable) == {"accounts", "installs", "overview"}
    assert "could not be reached" in snapshot.unavailable["installs"]
    assert "Prompts" not in report
    assert "*0*" not in report
    assert "hunter2" not in report
    assert "Unavailable today" in report


def test_a_cut_off_listing_sums_usage_from_the_calls_sorted_read_and_says_so() -> None:
    # Arrange: more accounts than one page. Newest-first covers today's
    # signups but not yesterday's; usage needs the active-first order.
    analytics = _FakeAnalytics(
        {
            ("users", TODAY, "registered"): _users(
                [_account("new", "2026-10-16"), _account("older", "2026-10-15")], matched=5
            ),
            ("users", TODAY, "calls"): _users(
                [
                    _account("p1", "2026-09-01", turns=4, sessions=1),
                    _account("p2", "2026-09-02", turns=6, sessions=1),
                ],
                matched=5,
            ),
            ("users", YESTERDAY, "calls"): _users([_account("p1", "2026-09-01", turns=1)]),
            ("installation_funnel", WEEK, ""): _installs(),
            ("product_overview", WEEK, ""): _overview(),
        }
    )

    # Act
    snapshot = collect_growth(CONFIG, day=DAY, call=analytics)

    # Assert
    accounts = snapshot.accounts
    assert accounts is not None
    assert accounts.prompts.value == 10
    assert accounts.partial
    assert accounts.new_accounts == DailyCount(1, None, None)
    assert "page limit" in render_growth_report(snapshot)


def test_an_account_name_cannot_ping_or_link_from_the_posted_report() -> None:
    # Arrange: someone signs up with Slack markup as their display name.
    analytics = _FakeAnalytics(
        {
            ("users", TODAY, "registered"): _users(
                [
                    _account(
                        "x",
                        "2026-10-16",
                        turns=9,
                        sessions=1,
                        name="<!channel> <https://phish.example|*Login*> @here",
                    )
                ]
            ),
            ("users", YESTERDAY, "calls"): _users([]),
            ("installation_funnel", WEEK, ""): _installs(),
            ("product_overview", WEEK, ""): _overview(),
        }
    )

    # Act
    snapshot = collect_growth(CONFIG, day=DAY, call=analytics)
    report = render_growth_report(snapshot)

    # Assert: the name survives as inert text, in the data and in the report.
    assert snapshot.accounts is not None
    name = snapshot.accounts.top_accounts[0].name
    assert not set(name) & set("<>@*|")
    assert "<!channel>" not in report
    assert "<https://" not in report
