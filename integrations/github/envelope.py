"""Response envelope shaping for GitHub tool results."""

from __future__ import annotations

import json
import re
from typing import Any

from config.constants.tool_discovery import TOOL_DISCOVERY_STATE_NO_MATCH, TOOL_FAILURE_STATE_KEY
from core.tool import ERROR_KIND_NOT_FOUND
from core.tool_framework.utils import tool_unavailable
from integrations.github.accessible_repos import accessible_repositories
from integrations.github.app_connection import github_setup_url

# GitHub's 404 as the REST client and the GitHub MCP server each word it.
_NOT_FOUND_RE = re.compile(r"GitHub API error 404\b|\b404 Not Found\b", re.IGNORECASE)
_MAX_REACHABLE_NAMED = 15


def _structured_content_or_text_fallback(result: dict[str, Any]) -> Any:
    """Return ``structured_content`` if present, else ``text`` parsed as JSON.

    Returns ``None`` when ``text`` is empty or not valid JSON.
    """
    structured = result.get("structured_content")
    if structured is not None:
        return structured
    text = str(result.get("text") or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def normalize_github_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw GitHub MCP tool result into the tool-framework payload.

    ``result`` is the dict returned by ``call_github_mcp_tool``: it carries
    ``is_error`` (bool), ``text`` (str, root-cause message on error),
    ``tool`` (str, the MCP tool name), ``arguments`` (dict passed to the tool),
    ``structured_content`` (parsed JSON or None), and ``content`` (list of MCP
    content items). When ``is_error`` is truthy, returns the standard
    ``tool_unavailable("github", ...)`` envelope so the framework surfaces a
    consistent unavailable-source response. Otherwise returns a dict with
    ``source="github"``, ``available=True``, and the original ``tool``,
    ``arguments``, ``text``, ``content`` keys preserved, and
    ``structured_content`` normalized via :func:`_structured_content_or_text_fallback`.
    """
    if result.get("is_error"):
        return tool_unavailable(
            "github",
            result.get("text") or "GitHub MCP tool call failed.",
            tool=result.get("tool"),
            arguments=result.get("arguments", {}),
        )
    return {
        "source": "github",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": _structured_content_or_text_fallback(result),
        "content": result.get("content", []),
    }


def missing_token_envelope(instruction: str, *, blocked: str) -> dict[str, Any]:
    """Block a GitHub operation with app recovery and the existing retry-stop contract."""
    url = github_setup_url()
    message = f"GitHub needs an authorized app connection, so {blocked}. Connect or reconnect GitHub in the OpenSRE app: {url}"
    return tool_unavailable(
        "github",
        f"{instruction} End the turn without retrying or invoking setup through slash_invoke.",
        response_text=message,
        setup_url=url,
        work_outcome={
            "status": "blocked",
            "reason": "github_connection_required",
            "summary": message,
        },
    )


def explain_not_found(
    result: dict[str, Any], *, token: str, owner: object = None, repo: object = None
) -> dict[str, Any]:
    """Mark a GitHub 404 as a lookup that found nothing, and say what can be found.

    GitHub answered, so the connection works: the result must not read as an
    outage, and the caller must not be told to reconnect. When the call named a
    repository this connection does not reach, the error also lists the ones it
    does, so the next call can use the right one instead of asking the user.
    Any other result is returned as it is.
    """
    error = result.get("error")
    if result.get("available") is not False or result.get("error_kind"):
        return result
    if not isinstance(error, str) or not _NOT_FOUND_RE.search(error):
        return result
    explained = {
        **result,
        "error_kind": ERROR_KIND_NOT_FOUND,
        TOOL_FAILURE_STATE_KEY: TOOL_DISCOVERY_STATE_NO_MATCH,
    }
    if not (isinstance(owner, str) and owner and isinstance(repo, str) and repo):
        return explained
    reachable = accessible_repositories(token)
    wanted = f"{owner}/{repo}".lower()
    if not reachable or any(found.full_name.lower() == wanted for found in reachable):
        return explained
    # Originals first: a fork is rarely the repository meant.
    names = [found.full_name for found in sorted(reachable, key=lambda found: found.fork)]
    listed = ", ".join(names[:_MAX_REACHABLE_NAMED])
    unlisted = len(names) - _MAX_REACHABLE_NAMED
    more = f" and {unlisted} more" if unlisted > 0 else ""
    explained["error"] = (
        f"{error} {owner}/{repo} is not a repository this GitHub connection reaches. "
        f"It reaches: {listed}{more}. If one of these is the repository meant, call the "
        "tool again with it and say which you used; ask the user only when none fits."
    )
    return explained
