"""Map LangGraph streaming events to human-readable reasoning steps.

Translates fine-grained ``events``-mode callbacks (tool calls, LLM
reasoning, chain transitions) into short status strings suitable for
spinner subtext in the terminal UI.
"""

from __future__ import annotations

from typing import Any

from app.tools.registry import resolve_tool_display_name

_NODE_VERB: dict[str, str] = {
    "extract_alert": "parsing",
    "resolve_integrations": "loading",
    "plan_actions": "planning",
    "investigate": "querying",
    "diagnose": "reasoning",
    "diagnose_root_cause": "reasoning",
    "publish": "formatting",
    "publish_findings": "formatting",
}


def tool_display_name(tool_name: str) -> str:
    """Return a human-friendly label for a tool, falling back to de-snaking."""
    return resolve_tool_display_name(tool_name)


def reasoning_text(kind: str, data: dict[str, Any], node_name: str) -> str | None:
    """Derive a short reasoning string from a LangGraph events-mode payload.

    Returns ``None`` when the event doesn't warrant a visible status update
    (e.g. internal chain scaffolding, empty chunks).
    """
    if kind == "on_tool_start":
        return _on_tool_start(data)
    if kind == "on_tool_end":
        return _on_tool_end(data, node_name)
    if kind == "on_chat_model_start":
        return _on_chat_model_start(node_name)
    if kind == "on_chat_model_stream":
        return _on_chat_model_stream(data)
    return None


def _on_tool_start(data: dict[str, Any]) -> str:
    name = data.get("name", "")
    display = tool_display_name(name) if name else "tool"
    return f"calling {display}"


def _on_tool_end(data: dict[str, Any], _node_name: str) -> str | None:
    output = data.get("data", {}).get("output", "")
    if isinstance(output, str) and len(output) > 120:
        output = output[:117] + "..."
    name = data.get("name", "")
    display = tool_display_name(name) if name else "tool"
    if output:
        return f"{display} returned"
    return f"{display} done"


def _on_chat_model_start(node_name: str) -> str:
    verb = _NODE_VERB.get(node_name, "thinking")
    return verb


def _on_chat_model_stream(data: dict[str, Any]) -> str | None:
    chunk = data.get("data", {}).get("chunk", {})
    if isinstance(chunk, dict):
        content: str = str(chunk.get("content", ""))
    else:
        content = str(chunk) if chunk else ""

    if not content or not content.strip():
        return None

    if len(content) > 60:
        content = content[:57] + "..."
    return content
