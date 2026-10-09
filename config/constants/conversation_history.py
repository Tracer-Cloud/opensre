"""Environment names and limits for the agent's cross-turn conversation history."""

from __future__ import annotations

# Set to 0/false/off to fall back to the text-only history block (kill switch).
OPENSRE_STRUCTURED_HISTORY_ENV = "OPENSRE_STRUCTURED_HISTORY"
# Estimated tokens of replayed history above which the session is compacted.
OPENSRE_HISTORY_TOKEN_BUDGET_ENV = "OPENSRE_HISTORY_TOKEN_BUDGET"
# Characters of one tool result kept in replayed history (head and tail).
OPENSRE_HISTORY_TOOL_RESULT_CHARS_ENV = "OPENSRE_HISTORY_TOOL_RESULT_CHARS"
# Set to 0/false/off to compact with the deterministic summary only.
OPENSRE_LLM_COMPACTION_ENV = "OPENSRE_LLM_COMPACTION"

#: Turns after which history is compacted even below the token budget, so many
#: short turns still get a model-written summary that has seen their tool output.
HISTORY_COMPACT_AFTER_TURNS = 60
#: Turns kept before the oldest fold into a text-only summary. A last-resort
#: backstop for when compaction cannot run; it starts well past the trigger above.
STRUCTURED_HISTORY_MAX_TURNS = 120
#: Turns kept verbatim, at most, when compaction runs.
HISTORY_KEEP_MAX_TURNS = 20
#: Estimated tokens of replayed history that trigger compaction.
DEFAULT_HISTORY_TOKEN_BUDGET = 48_000
#: Estimated tokens of the newest turns kept verbatim when compaction runs.
HISTORY_KEEP_RECENT_TOKENS = 16_000
#: Characters of one tool call's arguments kept in replayed history.
HISTORY_TOOL_ARGUMENTS_MAX_CHARS = 2_000
#: Characters of one assistant message kept in replayed history.
HISTORY_ASSISTANT_TEXT_MAX_CHARS = 8_000
#: Characters of a model-written compaction summary.
HISTORY_SUMMARY_MAX_CHARS = 12_000

__all__ = [
    "DEFAULT_HISTORY_TOKEN_BUDGET",
    "HISTORY_ASSISTANT_TEXT_MAX_CHARS",
    "HISTORY_COMPACT_AFTER_TURNS",
    "HISTORY_KEEP_MAX_TURNS",
    "HISTORY_KEEP_RECENT_TOKENS",
    "HISTORY_SUMMARY_MAX_CHARS",
    "HISTORY_TOOL_ARGUMENTS_MAX_CHARS",
    "OPENSRE_HISTORY_TOKEN_BUDGET_ENV",
    "OPENSRE_HISTORY_TOOL_RESULT_CHARS_ENV",
    "OPENSRE_LLM_COMPACTION_ENV",
    "OPENSRE_STRUCTURED_HISTORY_ENV",
    "STRUCTURED_HISTORY_MAX_TURNS",
]
