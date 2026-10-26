"""Characterization: ``dispatch_chat_turn`` is the common chat host API."""

from __future__ import annotations

from typing import Any

from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
from core.agent_harness.turns.headless_adapters import NullToolProvider
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _stub_turn_result(*, text: str = "ok") -> TurnResult:
    return TurnResult(
        final_intent="answered",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def test_dispatch_chat_turn_forwards_bindings_to_run_turn(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}
    expected = _stub_turn_result(text="ok")

    def _fake_run_turn(
        message: str,
        session: Any,
        *,
        execute_actions: Any,
        accounting: Any,
        confirm_fn: Any = None,
        is_tty: bool | None = None,
        surface: str = "interactive_shell",
        output: Any = None,
    ) -> TurnResult:
        seen.update(
            {
                "message": message,
                "session": session,
                "execute_actions": execute_actions,
                "accounting": accounting,
                "confirm_fn": confirm_fn,
                "is_tty": is_tty,
                "surface": surface,
                "output": output,
            }
        )
        return expected

    monkeypatch.setattr("core.agent_harness.turns.chat_api.run_turn", _fake_run_turn)

    session = object()
    execute = object()
    accounting = object()
    confirm = object()
    output = object()
    bindings = ChatTurnBindings(
        execute_actions=execute,  # type: ignore[arg-type]
        accounting=accounting,  # type: ignore[arg-type]
        confirm_fn=confirm,  # type: ignore[arg-type]
        is_tty=False,
        surface="gateway",
        output=output,  # type: ignore[arg-type]
    )

    result = dispatch_chat_turn("hello", session, bindings)  # type: ignore[arg-type]

    assert result is expected
    assert seen == {
        "message": "hello",
        "session": session,
        "execute_actions": execute,
        "accounting": accounting,
        "confirm_fn": confirm,
        "is_tty": False,
        "surface": "gateway",
        "output": output,
    }


def test_headless_agent_dispatch_uses_dispatch_chat_turn(monkeypatch: Any) -> None:
    expected = _stub_turn_result(text="via-api")
    captured: list[Any] = []

    def _fake_dispatch(message: str, session: Any, bindings: ChatTurnBindings) -> TurnResult:
        captured.append((message, session, bindings))
        return expected

    monkeypatch.setattr(
        "core.agent_harness.turns.headless_agent.dispatch_chat_turn",
        _fake_dispatch,
    )

    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    result = agent.dispatch("ping")

    assert result is expected
    assert len(captured) == 1
    message, session, bindings = captured[0]
    assert message == "ping"
    assert session is agent._session
    assert isinstance(bindings, ChatTurnBindings)
    assert bindings.is_tty is agent._is_tty


def test_only_chat_api_module_defines_dispatch_chat_turn() -> None:
    """Forbid a second host-facing ``dispatch_chat_turn`` definer."""
    import ast
    from pathlib import Path

    from tests.shared.product_sources import product_python_files

    root = Path(__file__).resolve().parents[3]
    definers: list[str] = []
    # Skips dot-directories, so a git worktree under ``.claude/`` is never ours.
    for path in product_python_files(root):
        if "tests" in path.relative_to(root).parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "dispatch_chat_turn":
                definers.append(path.relative_to(root).as_posix())

    assert definers == ["core/agent_harness/turns/chat_api.py"], definers
