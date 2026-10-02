"""Response envelope shaping for GitHub MCP tool results."""

from __future__ import annotations

import json
from typing import Any

from core.tool_framework.utils import tool_unavailable
from integrations.github.access import classify_github_access_failure
from integrations.github.client import GitHubApiError


def github_error_unavailable(exc: GitHubApiError, **extra: Any) -> dict[str, Any]:
    """Unavailable payload for a REST failure, with the access fix when there is one."""
    access = exc.access.as_payload() if exc.access is not None else {}
    return tool_unavailable("github", str(exc), **access, **extra)


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
        message = str(result.get("text") or "GitHub MCP tool call failed.")
        issue = classify_github_access_failure(status_code=None, message=message)
        return tool_unavailable(
            "github",
            f"{message}\n{issue.user_action}" if issue is not None else message,
            tool=result.get("tool"),
            arguments=result.get("arguments", {}),
            **(issue.as_payload() if issue is not None else {}),
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
