"""Invocation-scoped approval policy for ``opensre ask``."""

from __future__ import annotations

import threading
from collections.abc import Iterable

from core.agent_harness.tools import registered_single_turn_tool_names
from core.tool import (
    BeforeToolCallResult,
    RuntimeTool,
    SideEffectLevel,
    ToolExecutionHooks,
    ToolExecutionRequest,
)

_GATED_SIDE_EFFECTS = frozenset({SideEffectLevel.MUTATING, SideEffectLevel.EXTERNAL})


class ApprovalTracker:
    """Thread-safe record of tools denied during one invocation."""

    def __init__(self) -> None:
        self._denied: dict[str, None] = {}
        self._lock = threading.Lock()

    def record_denial(self, tool_name: str) -> None:
        """Record ``tool_name`` once while preserving first-seen order."""
        with self._lock:
            self._denied.setdefault(tool_name, None)

    @property
    def denied_tools(self) -> tuple[str, ...]:
        """Return denied tool names in first-seen order."""
        with self._lock:
            return tuple(self._denied)


def approval_required(tool: RuntimeTool) -> bool:
    """Return whether ``tool`` needs explicit authority for this invocation."""
    if bool(getattr(tool, "requires_approval", False)):
        return True
    level = getattr(tool, "side_effect_level", None)
    if level is None:
        return True
    return level in _GATED_SIDE_EFFECTS


def registered_ask_tool_names() -> frozenset[str]:
    """Return every registered tool name the ask action or gather phases can reach."""
    return registered_single_turn_tool_names()


def unknown_allowed_tools(allowed_tools: Iterable[str]) -> tuple[str, ...]:
    """Return sorted allowlist entries absent from ask's executable tool surfaces."""
    known = registered_ask_tool_names()
    return tuple(sorted({name for name in allowed_tools if name not in known}))


def build_approval_hooks(
    *,
    allowed_tools: Iterable[str],
    bypass_approvals: bool,
    tracker: ApprovalTracker,
) -> ToolExecutionHooks:
    """Build fail-closed hooks for one ``opensre ask`` invocation."""
    allowed = frozenset(allowed_tools)

    def before_tool_call(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        if not approval_required(request.tool):
            return None
        tool_name = request.tool_call.name
        if bypass_approvals or tool_name in allowed:
            return BeforeToolCallResult(
                approved=True,
                metadata={"ask_approval": "bypass" if bypass_approvals else "allowlist"},
            )
        tracker.record_denial(tool_name)
        return BeforeToolCallResult(
            blocked=True,
            terminate=True,
            reason=(
                f"Approval required for {tool_name}. Re-run with "
                f"--allowed-tool {tool_name}, or use "
                "--dangerously-bypass-approvals in a trusted environment."
            ),
            metadata={"ask_approval": "denied"},
        )

    return ToolExecutionHooks(before_tool_call=before_tool_call)


__all__ = [
    "ApprovalTracker",
    "approval_required",
    "build_approval_hooks",
    "registered_ask_tool_names",
    "unknown_allowed_tools",
]
