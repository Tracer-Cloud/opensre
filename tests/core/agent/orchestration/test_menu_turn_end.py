"""A queued user-choice menu must end the tool loop, so the model cannot re-ask."""

from __future__ import annotations

from typing import Any

from core.agent_harness.turns.action_menu_end import with_menu_turn_end
from core.llm.types import ToolCall
from core.tool.execution import ToolExecutionHooks, ToolExecutionPatch, ToolExecutionResult


def _request(name: str) -> Any:
    call = ToolCall(id=f"call-{name}", name=name, input={"title": "Which repository?"})
    return type("Request", (), {"tool_call": call, "arguments": call.input})()


def _session(pending: object) -> Any:
    return type("Session", (), {"pending_user_choice": pending})()


def test_queued_menu_terminates_the_loop() -> None:
    hooks = with_menu_turn_end(None, _session(pending=object()))

    patch = hooks.after_tool_call(
        _request("ask_user_choice"), ToolExecutionResult(content="queued")
    )

    assert patch is not None and patch.terminate is True


def test_skill_load_terminates_only_when_its_hook_queued_a_menu() -> None:
    """A skill's pre_execute menu ends the turn; a plain skill load does not."""
    queued = with_menu_turn_end(None, _session(pending=object()))
    plain = with_menu_turn_end(None, _session(pending=None))

    patch = queued.after_tool_call(_request("skill_view"), ToolExecutionResult(content="body"))

    assert patch is not None and patch.terminate is True
    assert (
        plain.after_tool_call(_request("skill_view"), ToolExecutionResult(content="body")) is None
    )


def test_unavailable_menu_does_not_terminate() -> None:
    no_menu = with_menu_turn_end(None, _session(pending=None))

    assert (
        no_menu.after_tool_call(_request("ask_user_choice"), ToolExecutionResult(content=""))
        is None
    )


def test_any_tool_terminates_once_a_menu_is_pending() -> None:
    hooks = with_menu_turn_end(None, _session(pending=object()))

    patch = hooks.after_tool_call(
        _request("scan_local_git_workspace"), ToolExecutionResult(content="scanned")
    )

    assert patch is not None and patch.terminate is True


def test_a_queued_menu_blocks_later_tools_in_the_same_batch() -> None:
    hooks = with_menu_turn_end(None, _session(pending=object()))

    blocked = hooks.before_tool_call(_request("analyze_github_ci_reliability"))
    transport = hooks.before_tool_call(_request("slash_invoke"))

    assert blocked is not None and blocked.blocked is True and blocked.terminate is True
    assert transport is None


def test_base_hook_patch_is_kept_and_marked_terminate() -> None:
    seen: list[str] = []

    def base_after(request: Any, _result: ToolExecutionResult) -> ToolExecutionPatch:
        seen.append(request.tool_call.name)
        return ToolExecutionPatch(content="rewritten")

    hooks = with_menu_turn_end(ToolExecutionHooks(after_tool_call=base_after), _session(object()))

    patch = hooks.after_tool_call(
        _request("ask_user_choice"), ToolExecutionResult(content="queued")
    )

    assert seen == ["ask_user_choice"]
    assert patch is not None and patch.content == "rewritten" and patch.terminate is True
