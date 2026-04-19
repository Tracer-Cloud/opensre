"""Base formatting utilities for report generation."""


def format_code_block(payload: str, language: str) -> str:
    """Wrap content in a markdown code block with syntax highlighting.

    Args:
        payload: Content to wrap
        language: Language identifier for syntax highlighting (json, text, python, etc.)

    Returns:
        Markdown-formatted code block
    """
    return f"```{language}\n{payload}\n```"


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
