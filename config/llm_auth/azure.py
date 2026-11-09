"""Pure Azure OpenAI config normalization (no first-party imports)."""

from __future__ import annotations


def normalize_azure_openai_base_url(value: str) -> str:
    """Normalize an Azure OpenAI resource endpoint URL."""
    base = (value or "").strip()
    if not base:
        return ""
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    return base.rstrip("/")


__all__ = ["normalize_azure_openai_base_url"]
