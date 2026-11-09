"""Agent-callable Slack workspace message search."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import SUMMARIZE_OBSERVATION_TAG, tool
from core.tool_framework.utils import tool_unavailable
from integrations.slack.tools.slack_read_messages_tool.constants import SOURCE
from integrations.slack.web_client import resolve_user_token, search_messages, user_token_configured

# Cap the query echoed into the evidence summary: the entry is re-read on every
# later turn, so a long search string costs context without adding signal.
_MAX_SUMMARY_QUERY_CHARS = 80


def _map_slack_search_messages(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Record workspace search hits as citeable evidence, linking the top match."""
    matches = output.get("matches")
    if not isinstance(matches, list) or not matches:
        return
    query = str(tool_input.get("query") or "").strip()[:_MAX_SUMMARY_QUERY_CHARS]
    scope = f" for {query!r}" if query else ""
    truncated = " (truncated)" if output.get("truncated") else ""
    top = matches[0] if isinstance(matches[0], dict) else {}
    record_evidence_entry(
        evidence,
        source="slack_search_messages",
        label="Slack Message Search",
        summary=f"{len(matches)} matches{scope}{truncated}",
        url=str(top.get("permalink") or "") or None,
    )


class SlackSearchMessagesTool(BaseTool):
    """Search Slack messages across the workspace."""

    name = "slack_search_messages"
    source = SOURCE
    evidence_mapper = _map_slack_search_messages
    description = (
        "Search Slack *messages* workspace-wide (search.messages). "
        "Use Slack search syntax (e.g. 'in:#incidents timeout', 'from:@user error'). "
        "Needs a Slack user token (SLACK_ACCESS_TOKEN, xoxp-…) with search:read — "
        "Slack refuses bot tokens for this endpoint. "
        "Not for workspace roster — use slack_list_team_members for who is on the team / member IDs."
    )
    use_cases = [
        "Finding prior discussion of an incident keyword",
        "Locating where a bug was reported in Slack",
    ]
    anti_examples = [
        'Answering "who is on the team?" (use slack_list_team_members)',
        "Reading one known channel's recent history (use slack_read_messages)",
        "Searching without a concrete query",
    ]
    tags = (SUMMARIZE_OBSERVATION_TAG,)
    requires = ["slack"]
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    requires_approval = False
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Slack search query string.",
            },
            "count": {
                "type": "integer",
                "description": "Max matches to return (1-100, default 20).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "'read' on success, 'failed' otherwise",
        "matches": "list of {channel_id, user, ts, text, permalink}",
        "match_count": "number of matches returned",
        "truncated": "true when the search hit the count cap and more matches may exist",
        "error": "error detail when status is 'failed'",
        "error_type": "validation_error, configuration_error, or api_error",
    }

    def is_available(self, _sources: dict[str, Any]) -> bool:
        # A bot token does not make this tool usable, so the resolved-integration
        # map is not consulted: only a user token counts.
        return user_token_configured()

    def run(self, query: str, count: int = 20, **_kwargs: Any) -> dict[str, Any]:
        target, resolution_error = resolve_user_token()
        if target is None:
            return tool_unavailable(
                SOURCE,
                resolution_error,
                status="failed",
                error_type="configuration_error",
                matches=[],
                match_count=0,
                truncated=False,
            )

        matches, error, truncated = search_messages(target, query=query, count=count)
        if matches is None:
            return {
                "source": SOURCE,
                "available": True,
                "status": "failed",
                "error": error,
                "error_type": ("validation_error" if "empty" in error else "api_error"),
                "matches": [],
                "match_count": 0,
                "truncated": False,
            }
        return {
            "source": SOURCE,
            "available": True,
            "status": "read",
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
        }


slack_search_messages = tool(
    SlackSearchMessagesTool(),
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
)
