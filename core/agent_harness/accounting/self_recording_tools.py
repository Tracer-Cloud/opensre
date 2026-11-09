"""Action tools that append their own ``session.history`` row when executed.

One catalogue: the shell's action observer and the generic tool-result
accounting both key off it, so a new tool cannot silently double-record a turn.
Leaf module — importable by the runner and by hosts without loading the agent
loop.
"""

from __future__ import annotations

SELF_RECORDING_ACTION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "cli_exec",
        "code_implement",
        "llm_set_provider",
        "shell_run",
        "slash_invoke",
        "task_cancel",
    }
)

__all__ = ["SELF_RECORDING_ACTION_TOOL_NAMES"]
