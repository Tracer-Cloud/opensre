"""Daily growth figures for one UTC day, read from the OpenSRE Analytics app.

Every figure comes from the app's own MCP tools, which run the same loaders as
its dashboard. Sums over accounts are computed here because the app reports
them per account. A source that fails leaves its section unavailable with a
short reason; it is never reported as zero.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

import httpx

from integrations.opensre_analytics.client import AnalyticsToolError, call_tool_json
from integrations.opensre_analytics.config import OpenSREAnalyticsConfig

logger = logging.getLogger(__name__)

#: The analytics app's ``users`` tool returns at most this many accounts per call.
USERS_PAGE_LIMIT = 1000
#: Days before the report day that the "7-day avg" comparison averages.
BASELINE_DAYS = 7
TOP_ACCOUNTS = 3
_TEAM_COHORT = "team"
#: Display names are chosen by whoever signs up. Drop what Slack reads as a
#: mention, link or formatting (``<!channel>``, ``<url|text>``, ``*bold*``) so a
#: name cannot ping or phish the channel the report is posted to.
_NAME_MARKUP = re.compile(r"[<>@*_~`|\\]|[\x00-\x1f\x7f]")
_MAX_NAME_CHARS = 40

#: ``(config, tool_name, arguments) -> JSON object``; raises when the tool fails.
CallJson = Callable[[OpenSREAnalyticsConfig, str, dict[str, object]], dict[str, Any]]


@dataclass(frozen=True)
class DailyCount:
    """One metric on the report day, the day before, and the trailing average."""

    value: int
    previous: int | None = None
    baseline_avg: float | None = None


@dataclass(frozen=True)
class TopAccount:
    """An account ranked by prompts on the report day."""

    name: str
    prompts: int


@dataclass(frozen=True)
class AccountFigures:
    """Signups and agent usage by OpenSRE accounts, team accounts excluded."""

    new_accounts: DailyCount | None
    """None when more accounts signed up than one listing returns."""
    active_accounts: DailyCount
    prompts: DailyCount
    """Completed agent turns: one per message a person sent the agent."""
    sessions: DailyCount
    tool_calls: DailyCount
    total_accounts: int
    top_accounts: tuple[TopAccount, ...]
    partial: bool
    """An activity listing hit the page limit, so usage sums are lower bounds."""


@dataclass(frozen=True)
class InstallFigures:
    """Installation funnel movement per UTC day."""

    new_installs: DailyCount
    account_linked: DailyCount
    activated: DailyCount
    llm_callers: DailyCount | None
    """None when the app could not read LLM callers for the window."""


@dataclass(frozen=True)
class OverviewFigures:
    """Product overview series, CI runs excluded by the app."""

    daily_active_users: DailyCount
    hosted_llm_calls: DailyCount


@dataclass(frozen=True)
class GrowthSnapshot:
    """Everything one daily growth report shows."""

    day: date
    accounts: AccountFigures | None
    installs: InstallFigures | None
    overview: OverviewFigures | None
    unavailable: Mapping[str, str] = field(default_factory=dict)
    """Section name -> short reason it could not be read."""
    dashboard_url: str = ""

    @property
    def available(self) -> bool:
        return any(part is not None for part in (self.accounts, self.installs, self.overview))


@dataclass(frozen=True)
class _UsersListing:
    rows: tuple[Mapping[str, Any], ...]
    total: int
    matched: int

    @property
    def complete(self) -> bool:
        return len(self.rows) >= self.matched


def report_day(now: datetime | None = None) -> date:
    """The last complete UTC day."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return current.date() - timedelta(days=1)


def day_range(start: date, end: date) -> str:
    """The analytics app's inclusive custom range for UTC calendar days."""
    return f"custom:{start.isoformat()}:{end.isoformat()}"


def failure_reason(err: BaseException) -> str:
    """A short, detail-free reason a source could not be read.

    The report is posted to chat channels, so it names the kind of failure
    only; the full error goes to the log.
    """
    if isinstance(err, AnalyticsToolError):
        return "the analytics source reported an outage"
    if isinstance(err, httpx.HTTPStatusError):
        if err.response.status_code == HTTPStatus.UNAUTHORIZED:
            return "the analytics API token was rejected"
        if err.response.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
            return "the analytics API is not enabled on the server"
    if isinstance(err, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "the analytics app could not be reached"
    if isinstance(err, TimeoutError):
        return "the analytics app timed out"
    return "the read failed"


def _int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _day_key(value: object) -> str:
    """``YYYY-MM-DD`` from an ISO date or timestamp (all UTC in this app)."""
    return str(value or "")[:10]


def display_name(value: object) -> str:
    """An account's display name as inert plain text."""
    cleaned = " ".join(_NAME_MARKUP.sub(" ", str(value or "")).split())
    if len(cleaned) > _MAX_NAME_CHARS:
        cleaned = cleaned[: _MAX_NAME_CHARS - 1].rstrip() + "…"
    return cleaned or "Unnamed account"


def _is_team(row: Mapping[str, Any]) -> bool:
    return row.get("cohort") == _TEAM_COHORT


def _is_active(row: Mapping[str, Any]) -> bool:
    return any(_int(row.get(key)) for key in ("turns", "sessions", "toolCalls"))


def _users(call: CallJson, config: OpenSREAnalyticsConfig, day: date, sort: str) -> _UsersListing:
    payload = call(
        config,
        "users",
        {"range": day_range(day, day), "sort": sort, "limit": USERS_PAGE_LIMIT, "cohort": "all"},
    )
    # The tool stays "available" when only one of its two sources answers; an
    # account list without activity (or without the directory) would sum to zero.
    sources = payload.get("sources")
    for source in ("directory", "activity"):
        part = sources.get(source) if isinstance(sources, Mapping) else None
        if isinstance(part, Mapping) and part.get("available") is False:
            raise AnalyticsToolError(f"users: the {source} source is unavailable.")
    rows = tuple(row for row in payload.get("users") or () if isinstance(row, Mapping))
    return _UsersListing(
        rows=rows,
        total=_int(payload.get("totalUsers")),
        matched=_int(payload.get("matchedUsers")) or len(rows),
    )


@dataclass(frozen=True)
class _Activity:
    active_accounts: int
    prompts: int
    sessions: int
    tool_calls: int
    top: tuple[TopAccount, ...]
    partial: bool


def _activity(listing: _UsersListing) -> _Activity:
    """Usage sums over non-team accounts in one day's listing.

    A ``calls``-sorted listing puts every active account before the inactive
    ones, so a full page whose last row is still active may have cut some off.
    """
    active = [row for row in listing.rows if not _is_team(row) and _is_active(row)]
    ranked = sorted(active, key=lambda row: _int(row.get("turns")), reverse=True)
    top = tuple(
        TopAccount(name=display_name(row.get("name")), prompts=prompts)
        for row in ranked[:TOP_ACCOUNTS]
        if (prompts := _int(row.get("turns"))) > 0
    )
    return _Activity(
        active_accounts=len(active),
        prompts=sum(_int(row.get("turns")) for row in active),
        sessions=sum(_int(row.get("sessions")) for row in active),
        tool_calls=sum(_int(row.get("toolCalls")) for row in active),
        top=top,
        partial=not listing.complete and bool(listing.rows) and _is_active(listing.rows[-1]),
    )


def _new_accounts(listing: _UsersListing, day: date) -> DailyCount | None:
    """Non-team signups per day from a newest-first listing.

    A truncated listing fully covers only the days after its oldest signup.
    """
    counts = Counter(
        _day_key(row.get("registeredAt"))
        for row in listing.rows
        if row.get("registeredAt") and not _is_team(row)
    )
    oldest = _day_key(listing.rows[-1].get("registeredAt")) if listing.rows else ""

    def covered(d: date) -> bool:
        return listing.complete or d.isoformat() > oldest

    if not covered(day):
        return None
    previous_day = day - timedelta(days=1)
    baseline_days = [day - timedelta(days=offset) for offset in range(1, BASELINE_DAYS + 1)]
    return DailyCount(
        value=counts[day.isoformat()],
        previous=counts[previous_day.isoformat()] if covered(previous_day) else None,
        baseline_avg=(
            sum(counts[d.isoformat()] for d in baseline_days) / BASELINE_DAYS
            if all(covered(d) for d in baseline_days)
            else None
        ),
    )


def _series(rows: object, field_name: str) -> dict[str, int]:
    """Date -> value; a day the source returned no row for had no events."""
    if not isinstance(rows, list):
        return {}
    return {
        _day_key(row.get("date")): _int(row.get(field_name))
        for row in rows
        if isinstance(row, Mapping) and row.get("date")
    }


def _daily(series: Mapping[str, int], day: date) -> DailyCount:
    baseline_days = [day - timedelta(days=offset) for offset in range(1, BASELINE_DAYS + 1)]
    return DailyCount(
        value=series.get(day.isoformat(), 0),
        previous=series.get((day - timedelta(days=1)).isoformat(), 0),
        baseline_avg=sum(series.get(d.isoformat(), 0) for d in baseline_days) / BASELINE_DAYS,
    )


def _installs(payload: Mapping[str, Any], day: date) -> InstallFigures:
    daily = payload.get("daily")
    callers = payload.get("dailyLlmCallers")
    return InstallFigures(
        new_installs=_daily(_series(daily, "installs"), day),
        account_linked=_daily(_series(daily, "authenticated"), day),
        activated=_daily(_series(daily, "activated"), day),
        llm_callers=None
        if payload.get("llmCallersError")
        else _daily(_series(callers, "users"), day),
    )


def _overview(payload: Mapping[str, Any], day: date) -> OverviewFigures:
    daily = payload.get("daily")
    return OverviewFigures(
        daily_active_users=_daily(_series(daily, "activeUsers"), day),
        hosted_llm_calls=_daily(_series(daily, "hostedLlmCalls"), day),
    )


def _run_all(jobs: Mapping[str, Callable[[], Any]]) -> tuple[dict[str, Any], dict[str, str]]:
    """Run independent reads concurrently; failures become per-job reasons."""
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except Exception as err:
                logger.warning("OpenSRE Analytics read %r failed", name, exc_info=err)
                errors[name] = failure_reason(err)
    return results, errors


def _dashboard_url(config: OpenSREAnalyticsConfig) -> str:
    parts = urlsplit(config.url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def collect_growth(
    config: OpenSREAnalyticsConfig,
    *,
    day: date | None = None,
    call: CallJson = call_tool_json,
) -> GrowthSnapshot:
    """Read every figure the daily growth report shows for ``day`` (default: yesterday UTC)."""
    day = day or report_day()
    previous_day = day - timedelta(days=1)
    window = day_range(day - timedelta(days=BASELINE_DAYS), day)
    results, errors = _run_all(
        {
            "accounts": lambda: _users(call, config, day, "registered"),
            "previous": lambda: _users(call, config, previous_day, "calls"),
            "installs": lambda: call(config, "installation_funnel", {"range": window}),
            "overview": lambda: call(config, "product_overview", {"range": window}),
        }
    )
    unavailable: dict[str, str] = {}

    accounts: AccountFigures | None = None
    registered: _UsersListing | None = results.get("accounts")
    if registered is None:
        unavailable["accounts"] = errors.get("accounts", "the read failed")
    else:
        current: _UsersListing | None = registered
        if not registered.complete:
            # Newest-first cannot be summed for usage once it is cut off.
            more, more_errors = _run_all({"accounts": lambda: _users(call, config, day, "calls")})
            current = more.get("accounts")
            if current is None:
                unavailable["accounts"] = more_errors.get("accounts", "the read failed")
        if current is not None:
            today = _activity(current)
            before = _activity(results["previous"]) if "previous" in results else None
            accounts = AccountFigures(
                new_accounts=_new_accounts(registered, day),
                active_accounts=DailyCount(
                    today.active_accounts, before.active_accounts if before else None
                ),
                prompts=DailyCount(today.prompts, before.prompts if before else None),
                sessions=DailyCount(today.sessions, before.sessions if before else None),
                tool_calls=DailyCount(today.tool_calls, before.tool_calls if before else None),
                total_accounts=registered.total,
                top_accounts=today.top,
                partial=today.partial or bool(before and before.partial),
            )

    installs = _installs(results["installs"], day) if "installs" in results else None
    if installs is None:
        unavailable["installs"] = errors.get("installs", "the read failed")
    overview = _overview(results["overview"], day) if "overview" in results else None
    if overview is None:
        unavailable["overview"] = errors.get("overview", "the read failed")

    return GrowthSnapshot(
        day=day,
        accounts=accounts,
        installs=installs,
        overview=overview,
        unavailable=unavailable,
        dashboard_url=_dashboard_url(config),
    )


__all__ = [
    "BASELINE_DAYS",
    "AccountFigures",
    "DailyCount",
    "GrowthSnapshot",
    "InstallFigures",
    "OverviewFigures",
    "USERS_PAGE_LIMIT",
    "TopAccount",
    "collect_growth",
    "day_range",
    "display_name",
    "failure_reason",
    "report_day",
]
