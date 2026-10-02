"""Response envelope shaping for GitHub tool results."""

from __future__ import annotations

import json
from typing import Any

from config.constants.github import GITHUB_INTEGRATION_SETUP_CLI, GITHUB_INTEGRATION_SETUP_SLASH
from core.tool_framework.utils import tool_unavailable


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
    """The ``tool_unavailable`` envelope for a GitHub tool that found no token.

    ``instruction`` tells the model how to hand the user the setup and stays in
    ``error``. ``response_text`` is the user's line — what is ``blocked`` and
    the command that connects GitHub, never tool calls or turn instructions — and
    closes a turn that ends on the queued wizard. ``setup_command`` marks the
    failure as waiting on that setup, so the turn may stop instead of retrying.
    """
    return tool_unavailable(
        "github",
        instruction,
        response_text=(
            f"GitHub isn't connected yet, so {blocked}. "
            f"Set it up with `{GITHUB_INTEGRATION_SETUP_CLI}`."
        ),
        setup_command=GITHUB_INTEGRATION_SETUP_SLASH,
    )
