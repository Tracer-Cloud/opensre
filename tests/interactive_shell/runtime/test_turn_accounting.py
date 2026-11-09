"""Tests for ShellTurnAccounting's pending turn LLM/error consumption."""

from __future__ import annotations

from typing import Any

from core.agent_harness.accounting.token_accounting import LlmRunInfo
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from infrastructure.analytics.prompt_log.recorder import PromptRecorder
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.session import Session


class _FakeRecorder:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.responses: list[tuple[str, Any]] = []
        self.flushed = 0

    def set_error(self, kind: str, message: str) -> None:
        self.errors.append((kind, message))

    def set_response(self, text: str, run: Any | None = None) -> None:
        self.responses.append((text, run))

    def set_run(self, run: Any) -> None:
        self.run = run

    def set_properties(self, properties: dict[str, Any]) -> None:
        self.properties = properties


def _result(*, text: str = "done") -> TurnResult:
    return TurnResult(
        final_intent="slash",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def test_finalize_applies_pending_turn_llm_when_no_conversational_run(monkeypatch) -> None:
    session = Session()
    pending = LlmRunInfo(model="claude-sonnet-4-5", provider="anthropic", input_tokens=100)
    session.terminal.set_pending_turn_llm(pending)
    recorder = _FakeRecorder()
    monkeypatch.setattr(PromptRecorder, "current", lambda: recorder)
    accounting = ShellTurnAccounting(session=session, text="/status")

    accounting.finalize(_result())

    assert recorder.run == pending
    assert recorder.properties["integration_snapshot_source"] == "runtime_config"
    assert session.terminal.pop_pending_turn_llm() is None


def test_finalize_does_not_duplicate_early_cli_agent_history(monkeypatch) -> None:
    """ActionRenderObserver records once; finalize must not append again."""
    session = Session()
    prompt = "Use the MySQL tool to query active connections."
    session.record("cli_agent", prompt)
    recorder = _FakeRecorder()
    monkeypatch.setattr(PromptRecorder, "current", lambda: recorder)
    accounting = ShellTurnAccounting(session=session, text=prompt)

    accounting.finalize(_result())

    cli_rows = [row for row in session.history if row.get("type") == "cli_agent"]
    assert len(cli_rows) == 1
    assert cli_rows[0]["text"] == prompt


def test_finalize_sets_structured_error_from_pending_turn_error(monkeypatch) -> None:
    session = Session()
    session.terminal.set_pending_turn_error("config", "ANTHROPIC_API_KEY not set")
    recorder = _FakeRecorder()
    monkeypatch.setattr(PromptRecorder, "current", lambda: recorder)
    accounting = ShellTurnAccounting(session=session, text="/status")

    accounting.finalize(_result(text="investigation_failed"))

    assert recorder.errors == [("config", "ANTHROPIC_API_KEY not set")]
    assert session.terminal.pop_pending_turn_error() is None


def test_finalize_consumes_pending_state_even_without_recorder() -> None:
    session = Session()
    session.terminal.set_pending_turn_llm(LlmRunInfo(model="m"))
    session.terminal.set_pending_turn_error("llm", "boom")
    accounting = ShellTurnAccounting(session=session, text="hi")

    accounting.finalize(_result())

    assert session.terminal.pop_pending_turn_llm() is None
    assert session.terminal.pop_pending_turn_error() is None
