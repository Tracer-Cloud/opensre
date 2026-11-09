from __future__ import annotations

import pytest

from core.llm.types import ToolCall
from core.tool.contracts import RegisteredTool, SideEffectLevel
from core.tool.execution import ToolExecutionRequest
from surfaces.cli.ask.approval import (
    ApprovalTracker,
    approval_required,
    build_approval_hooks,
)


def _tool(
    *,
    side_effect_level: SideEffectLevel | None,
    requires_approval: bool = False,
) -> RegisteredTool:
    return RegisteredTool(
        name="example_tool",
        description="Example tool",
        input_schema={"type": "object", "properties": {}},
        source="knowledge",
        run=lambda: {"ok": True},
        side_effect_level=side_effect_level,
        requires_approval=requires_approval,
    )


def _request(tool: RegisteredTool) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_call=ToolCall(id="call-1", name=tool.name, input={}),
        tool=tool,
        arguments={},
        source=tool.source,
        resolved_integrations={},
    )


@pytest.mark.parametrize("level", [SideEffectLevel.NONE, SideEffectLevel.READ_ONLY])
def test_explicit_safe_tools_run_without_approval(level: SideEffectLevel) -> None:
    assert approval_required(_tool(side_effect_level=level)) is False


@pytest.mark.parametrize(
    "level",
    [None, SideEffectLevel.MUTATING, SideEffectLevel.EXTERNAL],
)
def test_risky_or_unclassified_tools_require_approval(
    level: SideEffectLevel | None,
) -> None:
    assert approval_required(_tool(side_effect_level=level)) is True


def test_requires_approval_overrides_read_only_metadata() -> None:
    assert approval_required(
        _tool(side_effect_level=SideEffectLevel.READ_ONLY, requires_approval=True)
    )


def test_default_policy_denies_and_records_tool() -> None:
    tracker = ApprovalTracker()
    hooks = build_approval_hooks(
        allowed_tools=(),
        bypass_approvals=False,
        tracker=tracker,
    )

    assert hooks.before_tool_call is not None
    decision = hooks.before_tool_call(_request(_tool(side_effect_level=None)))

    assert decision is not None
    assert decision.blocked is True
    assert decision.terminate is True
    assert tracker.denied_tools == ("example_tool",)


def test_allowlist_matching_is_exact_and_case_sensitive() -> None:
    tracker = ApprovalTracker()
    hooks = build_approval_hooks(
        allowed_tools=("EXAMPLE_TOOL",),
        bypass_approvals=False,
        tracker=tracker,
    )

    assert hooks.before_tool_call is not None
    decision = hooks.before_tool_call(_request(_tool(side_effect_level=SideEffectLevel.MUTATING)))

    assert decision is not None
    assert decision.blocked is True
    assert tracker.denied_tools == ("example_tool",)


@pytest.mark.parametrize(
    ("allowed_tools", "bypass"),
    [(("example_tool",), False), ((), True)],
)
def test_explicit_authority_approves_tool(
    allowed_tools: tuple[str, ...],
    bypass: bool,
) -> None:
    tracker = ApprovalTracker()
    hooks = build_approval_hooks(
        allowed_tools=allowed_tools,
        bypass_approvals=bypass,
        tracker=tracker,
    )

    assert hooks.before_tool_call is not None
    decision = hooks.before_tool_call(_request(_tool(side_effect_level=None)))

    assert decision is not None
    assert decision.approved is True
    assert decision.blocked is False
    assert tracker.denied_tools == ()
