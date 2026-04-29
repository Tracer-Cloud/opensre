"""Shared text truncation utility."""


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    if len(text) <= limit:
        return text
    cut = max(0, limit - len(suffix))
    return text[:cut] + suffix
