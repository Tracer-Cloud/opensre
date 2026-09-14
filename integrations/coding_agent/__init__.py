"""Agent-neutral coding-agent seam.

Callers depend on this interface — a :class:`CodingResult`, a
:func:`run_coding_task` entry point, and :func:`verify_coding_agent` readiness —
rather than on a specific agent. Wired backends: Pi, Claude Code, Codex, and
Cursor; ``CODING_AGENT=auto`` (the default) picks the first ready one (see
:mod:`integrations.coding_agent.runner`).
"""

from __future__ import annotations

from integrations.coding_agent.config import (
    coding_agent_provider,
    coding_model,
    coding_timeout_seconds,
    coding_workspace,
)
from integrations.coding_agent.models import CodingResult, Progress
from integrations.coding_agent.runner import run_coding_task, verify_coding_agent

__all__ = [
    "CodingResult",
    "Progress",
    "coding_agent_provider",
    "coding_model",
    "coding_timeout_seconds",
    "coding_workspace",
    "run_coding_task",
    "verify_coding_agent",
]
