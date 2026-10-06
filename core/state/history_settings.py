"""Runtime switches and budgets for cross-turn history, read from the environment."""

from __future__ import annotations

import os

from config.constants.conversation_history import (
    DEFAULT_HISTORY_TOKEN_BUDGET,
    DEFAULT_HISTORY_TOOL_RESULT_CHARS,
    OPENSRE_HISTORY_TOKEN_BUDGET_ENV,
    OPENSRE_HISTORY_TOOL_RESULT_CHARS_ENV,
    OPENSRE_LLM_COMPACTION_ENV,
    OPENSRE_STRUCTURED_HISTORY_ENV,
)

_FALSY = frozenset({"0", "false", "no", "off"})
_MIN_TOKEN_BUDGET = 4_000
_MIN_TOOL_RESULT_CHARS = 500


def _flag_on(name: str) -> bool:
    """On unless the variable is set to a falsy value."""
    return os.getenv(name, "").strip().lower() not in _FALSY


def _positive_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw.isdigit():
        return default
    return max(minimum, int(raw))


def structured_history_enabled() -> bool:
    """Whether earlier turns are replayed as typed messages (default) or as one text block."""
    return _flag_on(OPENSRE_STRUCTURED_HISTORY_ENV)


def llm_compaction_enabled() -> bool:
    """Whether compaction asks a model for the handoff summary before the deterministic one."""
    return _flag_on(OPENSRE_LLM_COMPACTION_ENV)


def history_token_budget() -> int:
    """Estimated tokens of replayed history that trigger compaction."""
    return _positive_int(
        OPENSRE_HISTORY_TOKEN_BUDGET_ENV, DEFAULT_HISTORY_TOKEN_BUDGET, _MIN_TOKEN_BUDGET
    )


def history_tool_result_chars() -> int:
    """Characters of one tool result kept when a turn is recorded."""
    return _positive_int(
        OPENSRE_HISTORY_TOOL_RESULT_CHARS_ENV,
        DEFAULT_HISTORY_TOOL_RESULT_CHARS,
        _MIN_TOOL_RESULT_CHARS,
    )


__all__ = [
    "history_token_budget",
    "history_tool_result_chars",
    "llm_compaction_enabled",
    "structured_history_enabled",
]
