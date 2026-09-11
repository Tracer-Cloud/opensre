"""GitHub REST star-history tools."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from http import HTTPStatus
from math import ceil
from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_credentials_available,
    github_creds,
    github_source_available,
)

_DEFAULT_WINDOW_DAYS = 30
_MIN_WINDOW_DAYS = 1
_MAX_WINDOW_DAYS = 365
_PER_PAGE = 100
_MAX_PAGES_TO_SCAN = 30
_STAR_ACCEPT_HEADER = "application/vnd.github.star+json"


def _github_star_history_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        (
            github_source_available(sources)
            or gh.get("public_repository")
            or resolve_github_token(None)
        )
        and gh.get("owner")
        and gh.get("repo")
    )


def _github_star_history_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {
        "owner": gh.get("owner"),
        "repo": gh.get("repo"),
        "public_repository": bool(gh.get("public_repository")),
        **github_creds(gh),
    }


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _normalize_days(days: int | None) -> int:
    if days is None:
        return _DEFAULT_WINDOW_DAYS
    return min(max(int(days), _MIN_WINDOW_DAYS), _MAX_WINDOW_DAYS)


def _parse_github_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _daily_rows(start_day: date, days: int, counts: Counter[date]) -> list[dict[str, Any]]:
    return [
        {"date": (start_day + timedelta(days=offset)).isoformat(), "stars": counts[day]}
        for offset in range(days)
        for day in [start_day + timedelta(days=offset)]
    ]


def _github_stargazer_listing_error(exc: GitHubApiError) -> str:
    message = str(exc)
    if exc.status_code == HTTPStatus.UNAUTHORIZED:
        return (
            "GitHub authentication is required to read timestamped stargazer history; "
            "the current repository star count is still available from public metadata."
        )
    if exc.status_code in {
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    }:
        return (
            f"{message}. GitHub stargazer timestamp listings may require repository "
            "admin/collaborator access or token access for this endpoint; current "
            "repository star count can still be available from metadata."
        )
    return message


def _map_get_github_star_history(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    daily = output.get("daily", [])
    if daily:
        count = len(daily)
        word = "day" if count == 1 else "days"
        record_evidence_entry(
            evidence,
            source="get_github_star_history",
            label="GitHub Star History",
            summary=f"{count} {word} recorded",
        )


@tool(
    name="get_github_star_history",
    source="github",
    description=(
        "Fetch current GitHub repository stars and a fast day-by-day stargazer "
        "history window using the GitHub REST stargazers API."
    ),
    use_cases=[
        "Answering day-by-day GitHub stars for a repository",
        "Reporting GitHub stars gained over the last N days",
        "Computing repository star velocity or recent star growth",
    ],
    anti_examples=[
        "Current repository metadata only (use get_github_repository)",
        "Searching issues or pull requests (use search_github_issues or GitHub workflow tools)",
    ],
    requires=["owner", "repo"],
    outputs={
        "stargazers_count": "Current total stars on the repository",
        "daily": "Chronological rows with one UTC date and star count per requested day",
        "stars_in_window": "Total stars gained during the returned window",
        "complete": "Whether the requested window was fully scanned before the page cap",
    },
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "days": {
                "type": "integer",
                "minimum": _MIN_WINDOW_DAYS,
                "maximum": _MAX_WINDOW_DAYS,
                "description": "UTC days to include, default 30.",
            },
            "github_token": {"type": "string"},
            "public_repository": {"type": "boolean"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_star_history_available,
    is_advertised=github_credentials_available,
    extract_params=_github_star_history_extract_params,
    injected_params=(
        *GITHUB_INJECTED_PARAMS,
        "owner",
        "repo",
        "public_repository",
    ),
    evidence_mapper=_map_get_github_star_history,
)
def get_github_star_history(
    owner: str,
    repo: str,
    days: int | None = None,
    github_token: str | None = None,
    public_repository: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch a bounded, newest-first day-by-day GitHub star history window."""
    window_days = _normalize_days(days)
    now = _now_utc()
    end_day = now.date()
    start_day = end_day - timedelta(days=window_days - 1)
    window_start = datetime.combine(start_day, time.min, tzinfo=UTC)
    client = GitHubRestClient(
        github_token,
        allow_unauthenticated_read=public_repository,
    )
    repository_path = f"/repos/{owner}/{repo}"

    try:
        repo_payload = client.request("GET", repository_path)
    except GitHubApiError as exc:
        report_run_error(
            exc,
            tool_name="get_github_star_history",
            source="github",
            component="integrations.github.tools.stargazers",
            method="GitHubRestClient.request",
            extras={"owner": owner, "repo": repo, "stage": "repository"},
        )
        return tool_unavailable(
            "github",
            str(exc),
            owner=owner,
            repo=repo,
            repository=f"{owner}/{repo}",
            daily=[],
            stargazers_count=0,
        )

    if not isinstance(repo_payload, dict):
        return tool_unavailable(
            "github",
            "GitHub API returned an unexpected repository payload.",
            owner=owner,
            repo=repo,
            repository=f"{owner}/{repo}",
            daily=[],
            stargazers_count=0,
        )

    total_stars = int(repo_payload.get("stargazers_count") or 0)
    last_page = max(ceil(total_stars / _PER_PAGE), 1)
    counts: Counter[date] = Counter()
    latest_seen: datetime | None = None
    oldest_seen: datetime | None = None
    pages_scanned = 0
    complete = True
    warning = ""

    try:
        for page in range(last_page, 0, -1):
            if pages_scanned >= _MAX_PAGES_TO_SCAN:
                complete = False
                warning = (
                    f"Stopped after {_MAX_PAGES_TO_SCAN} newest stargazer pages to keep the "
                    "chat turn responsive; increase the window or run an offline export for "
                    "older high-volume history."
                )
                break
            raw_page = client.request(
                "GET",
                f"{repository_path}/stargazers",
                params={"per_page": _PER_PAGE, "page": page},
                accept=_STAR_ACCEPT_HEADER,
            )
            pages_scanned += 1
            if not isinstance(raw_page, list):
                return tool_unavailable(
                    "github",
                    "GitHub API returned an unexpected stargazer payload.",
                    owner=owner,
                    repo=repo,
                    repository=f"{owner}/{repo}",
                    daily=[],
                    stargazers_count=total_stars,
                    pages_scanned=pages_scanned,
                )
            page_timestamps = [
                parsed
                for item in raw_page
                if isinstance(item, dict)
                for parsed in [_parse_github_timestamp(item.get("starred_at"))]
                if parsed is not None
            ]
            if raw_page and not page_timestamps:
                return tool_unavailable(
                    "github",
                    "GitHub did not include stargazer timestamps for this request.",
                    owner=owner,
                    repo=repo,
                    repository=f"{owner}/{repo}",
                    daily=[],
                    stargazers_count=total_stars,
                    pages_scanned=pages_scanned,
                )
            if not page_timestamps:
                break

            latest_in_page = max(page_timestamps)
            oldest_in_page = min(page_timestamps)
            latest_seen = (
                latest_in_page if latest_seen is None else max(latest_seen, latest_in_page)
            )
            oldest_seen = (
                oldest_in_page if oldest_seen is None else min(oldest_seen, oldest_in_page)
            )

            for starred_at in page_timestamps:
                if starred_at >= window_start:
                    counts[starred_at.date()] += 1

            if oldest_in_page < window_start:
                break
    except GitHubApiError as exc:
        report_run_error(
            exc,
            tool_name="get_github_star_history",
            source="github",
            component="integrations.github.tools.stargazers",
            method="GitHubRestClient.request",
            extras={"owner": owner, "repo": repo, "stage": "stargazers"},
        )
        return tool_unavailable(
            "github",
            _github_stargazer_listing_error(exc),
            owner=owner,
            repo=repo,
            repository=f"{owner}/{repo}",
            daily=[],
            stargazers_count=total_stars,
            pages_scanned=pages_scanned,
        )

    stars_in_window = sum(counts.values())
    return {
        "source": "github",
        "available": True,
        "owner": owner,
        "repo": repo,
        "repository": f"{owner}/{repo}",
        "stargazers_count": total_stars,
        "window_days": window_days,
        "window_start": window_start.isoformat(timespec="seconds"),
        "window_end": now.isoformat(timespec="seconds"),
        "stars_in_window": stars_in_window,
        "stars_per_day": stars_in_window / window_days,
        "daily": _daily_rows(start_day, window_days, counts),
        "pages_scanned": pages_scanned,
        "per_page": _PER_PAGE,
        "complete": complete,
        "latest_starred_at": latest_seen.isoformat(timespec="seconds") if latest_seen else "",
        "oldest_starred_at_scanned": (
            oldest_seen.isoformat(timespec="seconds") if oldest_seen else ""
        ),
        "warning": warning,
    }
