"""Core runtime engine for the interactive shell.

Reusable session state lives in ``core.agent_harness.session`` and terminal runtime
context lives in ``interactive_shell.runtime.context``. This package owns the
remaining runtime engine concerns (mutable runtime state, prompt manager,
turn detection).
"""

from __future__ import annotations

from infrastructure.scheduling.task_registry import TaskRegistry

__all__ = ["TaskRegistry"]
