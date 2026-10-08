"""Shared constants for the tool contracts and registry."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# Approval tokens auto-expire after this many seconds (5 minutes).
DEFAULT_APPROVAL_EXPIRY_SECONDS: Final[int] = 300

# A workspace scan started before the model asked for it answers that call
# only when it finished within this many seconds.
WORKSPACE_SCAN_PREFETCH_MAX_AGE_SECONDS: Final[float] = 120.0
# The same bound for a CI reliability analysis started at the repository pick.
CI_ANALYSIS_PREFETCH_MAX_AGE_SECONDS: Final[float] = 300.0
# Background reads one prefetching tool keeps at once (one thread each).
TOOL_PREFETCH_MAX_ENTRIES: Final[int] = 4


class ToolBlockedBy(StrEnum):
    """The ``before_tool_call`` hook that refused a tool call.

    A refusing hook sets its member as a ``BeforeToolCallResult.metadata`` key
    (``HOOK_EXCEPTION`` holds the exception's type name, every other key
    ``True``). Analytics and the local trace report that member as ``blocked_by``.
    """

    DUPLICATE_ACTION = "duplicate_action"
    PLAN_REQUIRED = "plan_required"
    MENU_PENDING = "menu_pending"
    APPROVAL_DECLINED = "approval_declined"
    APPROVAL_PENDING = "approval_pending"
    HOOK_EXCEPTION = "hook_exception"


class ToolSkippedBy(StrEnum):
    """Why a requested tool call was skipped without running."""

    TURN_TERMINATED = "turn_terminated"
    HOST_CANCEL = "host_cancel"


__all__ = [
    "CI_ANALYSIS_PREFETCH_MAX_AGE_SECONDS",
    "DEFAULT_APPROVAL_EXPIRY_SECONDS",
    "TOOL_PREFETCH_MAX_ENTRIES",
    "WORKSPACE_SCAN_PREFETCH_MAX_AGE_SECONDS",
    "ToolBlockedBy",
    "ToolSkippedBy",
]
