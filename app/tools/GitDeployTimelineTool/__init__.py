"""Time-window deploy timeline tool for GitHub repos.

Sits alongside ``list_github_commits`` but asks a different question:

    ``list_github_commits`` — "show me the N most recent commits" (generic
    history browsing).

    ``get_git_deploy_timeline`` — "what landed on the default branch between
    T1 and T2" (incident correlation; "did something ship right before this
    alert fired?").

The tool is a thin wrapper on top of the MCP ``list_commits`` call, using the
GitHub REST API's ``since`` / ``until`` ISO-8601 timestamps. It normalizes
each commit into a compact shape with the fields an RCA agent actually needs
(authored/committed time, author, message subject line, sha). A
``window_minutes_before_alert`` convenience arg lets callers say "the 120
minutes before now" without having to compute the timestamps themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.integrations.github_mcp import call_github_mcp_tool
from app.tools.GitHubSearchCodeTool import (
    _gh_available,
    _gh_creds,
    _normalize_tool_result,
    _resolve_config,
)
from app.tools.tool_decorator import tool

DEFAULT_WINDOW_MINUTES = 120
MAX_WINDOW_MINUTES = 7 * 24 * 60  # 7 days
DEFAULT_PER_PAGE = 30
MAX_PER_PAGE = 100  # GitHub REST API hard cap for list_commits


def _parse_iso8601(value: str) -> datetime | None:
    """Parse ISO-8601 timestamp, accepting the trailing ``Z`` shorthand.

    Always returns a timezone-aware ``datetime``: naive input (no offset) is
    assumed to be UTC so downstream comparison and ``astimezone`` calls stay
    consistent. Returns None for empty or malformed input rather than raising,
    so the tool can fall back to its default window instead of failing the
    call.
    """
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _resolve_window(
    since: str, until: str, window_minutes_before_alert: int | None
) -> tuple[str, str]:
    """Resolve the [since, until] ISO-8601 window.

    Precedence:
      1. ``until`` is set to the parsed value if present; falls back to "now"
         on empty or malformed input (a malformed ``until`` does NOT invalidate
         ``since`` — the ``until`` anchor simply becomes "now").
      2. ``since`` is set to the parsed value if present; an inverted range
         (``since > until``) is treated as invalid and falls through to (3).
      3. If ``since`` is unset/invalid, compute it as ``until -
         window_minutes_before_alert`` (or ``DEFAULT_WINDOW_MINUTES`` if that
         arg is also unset/non-positive).

    The final span is always clamped to ``MAX_WINDOW_MINUTES`` to keep the
    MCP call bounded and avoid paging through months of history by accident.
    """
    now = datetime.now(UTC)

    parsed_since = _parse_iso8601(since)
    parsed_until = _parse_iso8601(until) or now

    # Inverted range (since > until) is always a caller error — we treat
    # ``since`` as invalid and fall through to the window-minutes branch
    # rather than passing an impossible range to the MCP server.
    if parsed_since is not None and parsed_since > parsed_until:
        parsed_since = None

    if parsed_since is None:
        minutes = window_minutes_before_alert
        if minutes is None or minutes <= 0:
            minutes = DEFAULT_WINDOW_MINUTES
        minutes = min(minutes, MAX_WINDOW_MINUTES)
        parsed_since = parsed_until - timedelta(minutes=minutes)

    # Clamp the span regardless of how it was specified.
    span = parsed_until - parsed_since
    if span > timedelta(minutes=MAX_WINDOW_MINUTES):
        parsed_since = parsed_until - timedelta(minutes=MAX_WINDOW_MINUTES)

    return (
        parsed_since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        parsed_until.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    )


def _summarize_commit(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten the MCP commit envelope into the fields a diagnose step cares about."""
    commit = raw.get("commit") or {}
    author = commit.get("author") or {}
    committer = commit.get("committer") or {}
    message = str(commit.get("message") or "")
    subject = message.splitlines()[0] if message else ""
    return {
        "sha": raw.get("sha", ""),
        "short_sha": str(raw.get("sha", ""))[:7],
        "author_name": author.get("name", ""),
        "author_date": author.get("date", ""),
        "committer_date": committer.get("date", ""),
        "message_subject": subject,
        "url": raw.get("html_url", ""),
    }


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources["github"]
    return {
        "owner": gh["owner"],
        "repo": gh["repo"],
        "branch": gh.get("branch") or gh.get("default_branch") or "main",
        **_gh_creds(gh),
    }


def _is_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(_gh_available(sources) and gh.get("owner") and gh.get("repo"))


@tool(
    name="get_git_deploy_timeline",
    source="github",
    description=(
        "List commits on a GitHub branch within a time window (defaults to the last "
        "120 minutes). Used to correlate an alert with recent deploys by asking "
        '"what changed right before this fired?"'
    ),
    use_cases=[
        "Correlating an incident with recent code changes on the default branch",
        "Checking whether a deploy landed within the alert window",
        "Building a short-form deploy timeline for RCA narrative",
    ],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "branch": {"type": "string", "default": "main"},
            "since": {
                "type": "string",
                "description": "ISO-8601 window start (e.g. 2026-04-20T10:00:00Z). Optional.",
            },
            "until": {
                "type": "string",
                "description": "ISO-8601 window end. Defaults to now.",
            },
            "window_minutes_before_alert": {
                "type": "integer",
                "description": (
                    "Convenience: minutes back from 'until' (or now) when 'since' is "
                    f"omitted. Clamped to {MAX_WINDOW_MINUTES} minutes."
                ),
                "default": DEFAULT_WINDOW_MINUTES,
            },
            "per_page": {
                "type": "integer",
                "default": DEFAULT_PER_PAGE,
                "minimum": 1,
                "maximum": MAX_PER_PAGE,
            },
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_is_available,
    extract_params=_extract_params,
)
def get_git_deploy_timeline(
    owner: str,
    repo: str,
    branch: str = "main",
    since: str = "",
    until: str = "",
    window_minutes_before_alert: int | None = DEFAULT_WINDOW_MINUTES,
    per_page: int = DEFAULT_PER_PAGE,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return commits on ``branch`` between ``since`` and ``until``."""
    config = _resolve_config(github_url, github_mode, github_token, github_command, github_args)
    if config is None:
        return {
            "source": "github",
            "available": False,
            "error": "GitHub MCP integration is not configured.",
            "commits": [],
            "window": {},
        }

    resolved_since, resolved_until = _resolve_window(since, until, window_minutes_before_alert)
    # Clamp per_page to the GitHub REST API maximum of 100. Values above this
    # are silently truncated upstream; we enforce the ceiling explicitly so
    # ``truncated`` below is meaningful.
    effective_per_page = max(1, min(per_page, MAX_PER_PAGE))

    arguments: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "sha": branch,
        "since": resolved_since,
        "until": resolved_until,
        "perPage": effective_per_page,
    }

    result = call_github_mcp_tool(config, "list_commits", arguments)
    payload = _normalize_tool_result(result)
    raw_commits = payload.pop("structured_content", None) or []
    if not isinstance(raw_commits, list):
        raw_commits = []

    commits = [_summarize_commit(item) for item in raw_commits if isinstance(item, dict)]
    # When the page is full we cannot tell from the API whether more commits
    # exist in the window — surface the uncertainty so the agent can choose to
    # narrow the window or raise per_page rather than concluding "nothing
    # else shipped".
    truncated = len(commits) >= effective_per_page
    payload.update(
        {
            "commits": commits,
            "commits_count": len(commits),
            "window": {
                "since": resolved_since,
                "until": resolved_until,
                "branch": branch,
                "per_page": effective_per_page,
                "truncated": truncated,
            },
        }
    )
    return payload
