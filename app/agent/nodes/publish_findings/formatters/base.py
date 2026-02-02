"""Base formatting utilities for report generation."""

import json
from typing import Any


def format_json_payload(data: Any, max_chars: int = 400) -> str:
    """Render JSON with a size cap for report output.

    Strategy:
    1. Try pretty-printed JSON (indented)
    2. Fall back to compact JSON if too large
    3. Truncate if still too large

    Args:
        data: Data to serialize as JSON
        max_chars: Maximum characters in output

    Returns:
        Formatted JSON string, truncated if necessary
    """
    # Try pretty format first
    pretty_payload = json.dumps(data, default=str, ensure_ascii=True, indent=2, sort_keys=True)
    if len(pretty_payload) <= max_chars:
        return pretty_payload

    # Fall back to compact format
    compact_payload = json.dumps(data, default=str, ensure_ascii=True)
    if len(compact_payload) <= max_chars:
        return compact_payload

    # Truncate if still too large
    return compact_payload[: max_chars - 3] + "..."


def format_code_block(payload: str, language: str) -> str:
    """Wrap content in a markdown code block with syntax highlighting.

    Args:
        payload: Content to wrap
        language: Language identifier for syntax highlighting (json, text, python, etc.)

    Returns:
        Markdown-formatted code block
    """
    return f"```{language}\n{payload}\n```"


def format_json_block(payload: str) -> str:
    """Wrap JSON content in a markdown code block.

    Args:
        payload: JSON string to wrap

    Returns:
        Markdown-formatted JSON code block
    """
    return format_code_block(payload, "json")


def format_text_block(payload: str) -> str:
    """Wrap text content in a markdown code block.

    Args:
        payload: Text string to wrap

    Returns:
        Markdown-formatted text code block
    """
    return format_code_block(payload, "text")


def shorten_text(text: str, max_chars: int = 120, suffix: str = "...") -> str:
    """Shorten text to a maximum length.

    Args:
        text: Text to shorten
        max_chars: Maximum characters in output (including suffix)
        suffix: Suffix to append when truncated

    Returns:
        Shortened text with suffix if truncated
    """
    # Clean up whitespace
    cleaned = " ".join(text.split())

    if len(cleaned) <= max_chars:
        return cleaned

    return cleaned[: max_chars - len(suffix)] + suffix


def format_slack_link(label: str, url: str | None) -> str:
    """Return a Slack-formatted hyperlink, falling back to plain text."""
    if not url:
        return label

    safe_label = label.replace("|", "¦").strip() or url
    return f"<{url}|{safe_label}>"
