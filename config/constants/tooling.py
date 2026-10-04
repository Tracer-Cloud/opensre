"""Shared constants for the tool contracts and registry."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# Approval tokens auto-expire after this many seconds (5 minutes).
DEFAULT_APPROVAL_EXPIRY_SECONDS: Final[int] = 300


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


__all__ = ["DEFAULT_APPROVAL_EXPIRY_SECONDS", "ToolBlockedBy", "ToolSkippedBy"]
