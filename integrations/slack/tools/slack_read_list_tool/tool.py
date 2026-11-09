"""Agent-callable Slack Lists find + read (team task boards, etc.)."""

from __future__ import annotations

import os
import re
from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.slack.tools.slack_read_list_tool.constants import (
    DEFAULT_ITEM_LIMIT,
    MAX_ITEM_LIMIT,
    SOURCE,
)
from integrations.slack.web_client import (
    bot_token_configured,
    fetch_slack_list_items,
    find_slack_lists,
    resolve_bot_token,
)

# A Slack List id is a file id: the letter "F" then 5+ alphanumerics (e.g. "F0123ABCD").
_SLACK_LIST_ID_CHARS = r"F[A-Z0-9]{5,}"
# Matches a value that is itself a bare list id.
_SLACK_LIST_ID_RE = re.compile(rf"^{_SLACK_LIST_ID_CHARS}$", re.IGNORECASE)
# Extracts a list id embedded in a Slack List URL path (e.g. ".../lists/F0123ABCD").
_SLACK_LIST_ID_IN_URL_RE = re.compile(rf"/({_SLACK_LIST_ID_CHARS})\b", re.IGNORECASE)


def _result(
    *,
    status: str,
    list_id: str = "",
    list_title: str = "",
    lists: list[dict[str, str]] | None = None,
    items: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    error: str = "",
    error_type: str = "",
) -> dict[str, Any]:
    """Build a ``slack_read_list`` payload for the ``available=True`` paths.

    ``error`` / ``error_type`` are included only when set, so success payloads
    stay free of empty error keys. ``truncated`` marks a partial row read so the
    agent does not treat the returned rows as the whole list.
    """
    rows = items or []
    payload: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "status": status,
        "list_id": list_id,
        "list_title": list_title,
        "lists": lists or [],
        "items": rows,
        "item_count": len(rows),
        "truncated": truncated,
    }
    for key, value in (("error", error), ("error_type", error_type)):
        if value:
            payload[key] = value
    return payload


# What _resolve_list_id returns: (list_id, list_title, candidate lists, error).
_ListResolution = tuple[str, str, list[dict[str, str]], str]


def _unresolved(error: str, *, candidates: list[dict[str, str]] | None = None) -> _ListResolution:
    """No single list chosen — an error, or (empty error) ambiguous candidates."""
    return "", "", candidates or [], error


def _resolved(list_id: str) -> _ListResolution:
    """A list id resolved directly from input or the env default."""
    return list_id, "", [], ""


def _matched(row: dict[str, str], candidates: list[dict[str, str]]) -> _ListResolution:
    """A single matched list row, carrying the candidate set for context."""
    return row["list_id"], row.get("title") or row.get("name") or "", candidates, ""


def _map_slack_read_list(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Record the rows read from a Slack List as citeable evidence."""
    items = output.get("items")
    if not isinstance(items, list) or not items:
        return
    name = str(output.get("list_title") or output.get("list_id") or "").strip()
    scope = f" in {name}" if name else ""
    truncated = " (truncated)" if output.get("truncated") else ""
    record_evidence_entry(
        evidence,
        source="slack_read_list",
        label="Slack List",
        summary=f"{len(items)} rows{scope}{truncated}",
    )


class SlackReadListTool(BaseTool):
    """Find Slack Lists by name and/or read their rows (lists:read + files:read)."""

    name = "slack_read_list"
    source = SOURCE
    evidence_mapper = _map_slack_read_list
    description = (
        "Find and/or read a Slack *List* (the Lists product — e.g. a shared "
        "'OpenSRE Team Tasks' board), NOT channel message history and NOT the "
        "OpenSRE /tasks session store. Pass list_id (F…) when known, or "
        "name_query to discover Lists by title. Omit both to use "
        "SLACK_TEAM_TASKS_LIST_ID when set, otherwise search for common team-"
        "task titles. Returns list metadata + rows (name, assignees, status, "
        "due_date, fields). Requires lists:read (rows) and files:read "
        "(discovery via files.list)."
    )
    use_cases = [
        "Answering 'what are the OpenSRE team tasks?' from a Slack List",
        "Summarizing assignees / due dates on a shared Slack List",
        "Finding the list_id for 'OpenSRE Team Tasks' by name",
    ]
    anti_examples = [
        "Reading recent channel chat (use slack_read_messages)",
        "OpenSRE in-session /tasks (use slash_invoke /tasks)",
        "Downloading a normal file attachment from a message",
    ]
    requires = ["slack"]
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    requires_approval = False
    input_schema = {
        "type": "object",
        "properties": {
            "list_id": {
                "type": "string",
                "description": (
                    "Slack List id (F…) or a Slack List URL containing F…. "
                    "Omit to discover by name_query / env default."
                ),
            },
            "name_query": {
                "type": "string",
                "description": (
                    "Substring to match List title/name when list_id is unknown "
                    "(e.g. 'OpenSRE Team Tasks' or 'team tasks')."
                ),
            },
            "limit": {
                "type": "integer",
                "description": f"Max rows to return (1-{MAX_ITEM_LIMIT}, default {DEFAULT_ITEM_LIMIT}).",
            },
            "include_archived": {
                "type": "boolean",
                "description": "If true, request archived rows (default false).",
            },
        },
        "additionalProperties": False,
    }
    outputs = {
        "status": "'read' on success, 'failed' otherwise",
        "list_id": "resolved Slack List id",
        "list_title": "List title when known from discovery",
        "lists": "candidate Lists when discovery returns multiple matches",
        "items": "list of {id, name, assignees, status, due_date, archived, fields}",
        "item_count": "number of rows returned",
        "error": "error detail when status is 'failed'",
        "error_type": "validation_error, configuration_error, or api_error",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bot_token_configured(sources)

    def run(
        self,
        list_id: str = "",
        name_query: str = "",
        limit: int = DEFAULT_ITEM_LIMIT,
        include_archived: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        target, resolution_error = resolve_bot_token()
        if target is None:
            return tool_unavailable(
                SOURCE,
                resolution_error,
                status="failed",
                error_type="configuration_error",
                list_id="",
                list_title="",
                lists=[],
                items=[],
                item_count=0,
            )

        resolved_id, list_title, lists, resolve_error = self._resolve_list_id(
            target, list_id=list_id, name_query=name_query
        )
        identity: dict[str, Any] = {
            "list_id": resolved_id,
            "list_title": list_title,
            "lists": lists,
        }
        if resolve_error:
            kind = "validation_error" if "must be" in resolve_error else "api_error"
            return _result(status="failed", **identity, error=resolve_error, error_type=kind)
        if not resolved_id:
            multiple = "Multiple Slack Lists matched; pass list_id to read one." if lists else ""
            return _result(status="read", lists=lists, error=multiple)

        clamped = max(1, min(int(limit or DEFAULT_ITEM_LIMIT), MAX_ITEM_LIMIT))
        items, error, truncated = fetch_slack_list_items(
            target, list_id=resolved_id, limit=clamped, include_archived=bool(include_archived)
        )
        if items is None:
            return _result(status="failed", **identity, error=error, error_type="api_error")
        return _result(status="read", **identity, items=items, truncated=truncated)

    def _resolve_list_id(
        self,
        target: Any,
        *,
        list_id: str,
        name_query: str,
    ) -> _ListResolution:
        explicit = _extract_list_id(list_id)
        if list_id.strip() and not explicit:
            return _unresolved("list_id must be a Slack List id (F…) or List URL.")
        if explicit:
            return _resolved(explicit)

        env_id = _extract_list_id(str(os.environ.get("SLACK_TEAM_TASKS_LIST_ID") or "").strip())
        query = str(name_query or "").strip()
        if not query and env_id:
            return _resolved(env_id)
        query = query or "team tasks"

        found, err = find_slack_lists(target, name_query=query, limit=10)
        if found is None:
            return _unresolved(err)
        if not found:
            return _unresolved(
                f"No Slack Lists matched {query!r}. Pass list_id (F…) from the "
                "List URL, or set SLACK_TEAM_TASKS_LIST_ID."
            )
        if len(found) == 1:
            return _matched(found[0], found)
        # Prefer an exact title/name match (case-insensitive) when several hit.
        exact = [
            row
            for row in found
            if query.lower() in (row.get("title") or "").lower()
            or query.lower() == (row.get("name") or "").lower()
        ]
        if len(exact) == 1:
            return _matched(exact[0], found)
        return _unresolved("", candidates=found)


def _extract_list_id(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if _SLACK_LIST_ID_RE.match(upper):
        return upper
    match = _SLACK_LIST_ID_IN_URL_RE.search(upper)
    if match:
        return match.group(1)
    return ""


slack_read_list = tool(
    SlackReadListTool(),
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
)
