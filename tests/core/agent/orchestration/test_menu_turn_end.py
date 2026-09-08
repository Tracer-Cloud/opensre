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


def test_unavailable_menu_or_other_tools_do_not_terminate() -> None:
    no_menu = with_menu_turn_end(None, _session(pending=None))
    other = with_menu_turn_end(None, _session(pending=object()))

    assert (
        no_menu.after_tool_call(_request("ask_user_choice"), ToolExecutionResult(content=""))
        is None
    )
    assert other.after_tool_call(_request("shell_run"), ToolExecutionResult(content="")) is None


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
