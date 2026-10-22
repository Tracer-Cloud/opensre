"""Prompt redraws use a route snapshot refreshed outside the rendering callback."""

from __future__ import annotations

import threading
from io import StringIO

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.account import AccountLLMRoute
from surfaces.interactive_shell.runtime import shell_turn_execution
from surfaces.interactive_shell.runtime.core.prompt_builder import PromptBuilder
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.runtime.turn_host import AgentTurnResources, run_agent_turn
from surfaces.interactive_shell.session import Session


@pytest.mark.asyncio
async def test_redraws_do_not_resolve_credentials_and_completed_turn_refreshes_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current: list[AccountLLMRoute | None] = [
        AccountLLMRoute(base_url="https://example.test", model="hosted-model")
    ]
    reads: list[int] = []

    def route() -> AccountLLMRoute | None:
        reads.append(threading.get_ident())
        return current[0]

    def switch(*_args: object, **_kwargs: object) -> None:
        current[0] = None
        monkeypatch.setenv("LLM_PROVIDER", "chatgpt")
        monkeypatch.setenv("CHATGPT_MODEL", "local-model")

    monkeypatch.setattr("config.account.account_llm_route", route)
    monkeypatch.setattr(shell_turn_execution, "execute_shell_turn", switch)
    session = Session()
    state, spinner = ReplState(), SpinnerState()
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        builder = PromptBuilder(session, state, spinner)
        builder.setup()
        try:
            for _ in range(5):
                assert "openai / hosted-model" in builder._status_line()
            assert len(reads) == 1
            runtime = AgentTurnResources(
                session=session,
                state=state,
                spinner=spinner,
                invalidate_prompt=lambda: None,
                console=Console(file=StringIO()),
            )
            await run_agent_turn(runtime, "/model set chatgpt local-model")
            for _ in range(5):
                assert "chatgpt / local-model" in builder._status_line()
            assert len(reads) == 2
            assert reads[-1] != threading.get_ident()
        finally:
            await builder.close()
