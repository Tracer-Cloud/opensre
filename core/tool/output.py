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


# The truncation marker and shell status sit outside the retained body. Twenty
# percent of a tiny token limit is smaller than those extras, so replay would
# otherwise cut text the model already saw.
_HISTORY_OUTSIDE_BODY_SLACK_BYTES = 256


def history_replay_byte_budget() -> int:
    """Byte ceiling for replaying an observation that was already bounded."""
    return max(
        tool_output_byte_budget(history=True),
        tool_output_byte_budget() + _HISTORY_OUTSIDE_BODY_SLACK_BYTES,
    )


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


def _bound_text_blocks(content: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Keep the beginning and end of every text block, and every non-text block.

    Later text joins the first text block so a short final diagnostic survives
    the same head/tail cut as a single string. Non-text blocks stay in place.
    """
    text_parts = [str(block["text"]) for block in content if isinstance(block.get("text"), str)]
    if not text_parts:
        return list(content)
    joined = "\n".join(text_parts)
    if len(joined.encode("utf-8")) <= budget:
        return list(content)
    truncated = truncate_output_text(joined, budget)
    visible: list[dict[str, Any]] = []
    placed = False
    for block in content:
        if isinstance(block.get("text"), str):
            if not placed:
                visible.append({**block, "text": truncated})
                placed = True
            continue
        visible.append(block)
    return visible


def tool_output_content(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Bound text observations without changing retained results or non-text blocks."""
    budget = tool_output_byte_budget()
    if isinstance(content, str):
        return truncate_output_text(content, budget)
    return _bound_text_blocks(content, budget)
