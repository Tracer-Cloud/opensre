"""Shared text truncation utility."""


def truncate(text: str, limit: int, ellipsis: str = "...") -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(ellipsis)] + ellipsis
