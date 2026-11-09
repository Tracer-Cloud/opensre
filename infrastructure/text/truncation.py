"""Shared text truncation utility."""


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: limit - len(suffix)] + suffix


def clip_line(text: str, limit: int) -> str:
    """``text`` on one line (whitespace runs collapsed), cut to ``limit`` with an ellipsis."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
