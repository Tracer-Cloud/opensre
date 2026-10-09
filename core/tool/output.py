"""Codex-compatible UTF-8 budgets for provider-visible tool output."""

from __future__ import annotations

import os
from typing import Any

from config.constants.tool_output import (
    DEFAULT_TOOL_OUTPUT_TOKENS,
    OPENSRE_TOOL_OUTPUT_TOKEN_LIMIT_ENV,
    TOOL_OUTPUT_BYTES_PER_TOKEN,
    TOOL_OUTPUT_HISTORY_DENOMINATOR,
    TOOL_OUTPUT_HISTORY_NUMERATOR,
)


def tool_output_token_limit() -> int:
    """Return the configured approximate-token budget, defaulting to 10,000."""
    raw = os.getenv(OPENSRE_TOOL_OUTPUT_TOKEN_LIMIT_ENV, "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_TOOL_OUTPUT_TOKENS


def tool_output_byte_budget(*, history: bool = False) -> int:
    """Return the UTF-8 budget, allowing 20% serialization overhead in history."""
    tokens = tool_output_token_limit()
    if history:
        tokens = (
            tokens * TOOL_OUTPUT_HISTORY_NUMERATOR + TOOL_OUTPUT_HISTORY_DENOMINATOR - 1
        ) // TOOL_OUTPUT_HISTORY_DENOMINATOR
    return tokens * TOOL_OUTPUT_BYTES_PER_TOKEN


def truncate_output_text(text: str, max_bytes: int, *, tokens: bool = True) -> str:
    """Keep equal UTF-8 prefix/suffix budgets with Codex's middle omission marker."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    left_budget = max(max_bytes, 0) // 2
    right_budget = max(max_bytes, 0) - left_budget
    left = encoded[:left_budget].decode("utf-8", errors="ignore")
    right = encoded[-right_budget:].decode("utf-8", errors="ignore") if right_budget else ""
    if tokens:
        omitted = (len(encoded) - max(max_bytes, 0) + TOOL_OUTPUT_BYTES_PER_TOKEN - 1) // (
            TOOL_OUTPUT_BYTES_PER_TOKEN
        )
        unit = "tokens"
    else:
        omitted = len(text) - len(left) - len(right)
        unit = "chars"
    return f"{left}…{omitted} {unit} truncated…{right}"


def tool_output_content(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Bound text observations without changing retained results or non-text blocks."""
    budget = tool_output_byte_budget()
    if isinstance(content, str):
        return truncate_output_text(content, budget)
    visible: list[dict[str, Any]] = []
    omitted = 0
    remaining_tokens = tool_output_token_limit()
    for block in content:
        text = block.get("text")
        if not isinstance(text, str):
            visible.append(block)
            continue
        if remaining_tokens == 0:
            omitted += 1
            continue
        cost = (len(text.encode("utf-8")) + TOOL_OUTPUT_BYTES_PER_TOKEN - 1) // (
            TOOL_OUTPUT_BYTES_PER_TOKEN
        )
        visible.append(
            {
                **block,
                "text": truncate_output_text(text, remaining_tokens * TOOL_OUTPUT_BYTES_PER_TOKEN),
            }
        )
        remaining_tokens = max(remaining_tokens - cost, 0)
    if omitted:
        visible.append({"type": "text", "text": f"[omitted {omitted} text items ...]"})
    return visible
