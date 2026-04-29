"""Shared text truncation utility."""


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: limit - len(suffix)] + suffix
