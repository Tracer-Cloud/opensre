"""Community and contributor follow-up summary tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_credentials_available,
    github_creds,
    github_source_available,
)
from integrations.github.tools.community_followup_tool.mapper import (
    _map_summarize_community_followups,
)
from integrations.github.tools.workflow import summarize_community_followups_from_comments


def _community_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        (github_source_available(sources) or resolve_github_token(None))
        and gh.get("owner")
        and gh.get("repo")
    )


def _community_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


@tool(
    name="summarize_community_followups",
    source="github",
    description="Summarize unanswered community questions, meeting agenda items, and suggested replies from GitHub issue comments.",
    use_cases=[
        "Finding unanswered contributor questions in GitHub issue comments",
        "Preparing community meeting agenda follow-ups",
        "Drafting suggested replies without mutating GitHub or messaging platforms",
    ],
    anti_examples=["Posting replies", "Changing GitHub labels or assignees"],
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    evidence_mapper=_map_summarize_community_followups,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "comments": {"type": "array"},
            "maintainer_logins": {"type": "array", "items": {"type": "string"}},
            "per_page": {"type": "integer"},
            "since_days": {
                "type": "integer",
                "default": 30,
                "description": "Only consider comments updated in the last N days.",
            },
            "github_token": {"type": "string"},
        },
        "required": [],
    },
    is_available=_community_available,
    is_advertised=github_credentials_available,
    extract_params=_community_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
)
def summarize_community_followups(
    owner: str = "",
    repo: str = "",
    comments: list[dict[str, Any]] | None = None,
    maintainer_logins: list[str] | None = None,
    per_page: int = 100,
    since_days: int = 30,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    try:
        if comments is not None:
            normalized_comments = comments
        else:
            # Upper bound avoids OverflowError from timedelta's own bounded
            # range (~2.7M days) on an absurd caller-supplied value; 10 years is
            # already far beyond what "recent" means for this tool.
            bounded_since_days = min(max(1, since_days), 3650)
            since = datetime.now(UTC) - timedelta(days=bounded_since_days)
            normalized_comments = GitHubRestClient(github_token).paginate(
                f"/repos/{owner}/{repo}/issues/comments",
                params={
                    "per_page": max(1, min(per_page, 100)),
                    "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "sort": "updated",
                    "direction": "desc",
                },
            )
    except GitHubApiError as exc:
        return tool_unavailable(
            "github",
            str(exc),
            unanswered_questions=[],
            agenda_items=[],
            suggested_replies=[],
            side_effects=[],
        )

    summary = summarize_community_followups_from_comments(
        comments=normalized_comments,
        maintainer_logins=maintainer_logins,
    )
    return {"source": "github", "available": True, **summary}
