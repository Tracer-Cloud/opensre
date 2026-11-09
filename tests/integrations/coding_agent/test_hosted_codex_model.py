"""A hosted Codex run asks for the model the hosted route serves, not Codex's own default."""

from __future__ import annotations

from typing import Any

import pytest

from config.account import AccountLLMRoute
from integrations.coding_agent import runner
from integrations.coding_agent.models import CodingResult


def test_hosted_codex_defaults_to_the_routes_model_and_keeps_an_explicit_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the process holds hosted credentials; the backend run is recorded, not executed
    seen: list[str | None] = []

    def run(task: str, **kwargs: Any) -> CodingResult:
        seen.append(kwargs.get("model"))
        return CodingResult(success=True, summary="ok")

    def verify() -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setitem(runner._BACKENDS, "codex", (run, verify))
    monkeypatch.setattr(runner, "hosted_openai_subprocess_env", lambda: {"OPENAI_API_KEY": "t"})
    monkeypatch.setattr(
        runner,
        "account_llm_route",
        lambda: AccountLLMRoute(base_url="https://app.example/api/llm/v1", model="gpt-5.6-sol"),
    )

    # Act
    runner.run_coding_task("t", workspace="", model=None, timeout_sec=1, provider="codex")
    runner.run_coding_task("t", workspace="", model="o3", timeout_sec=1, provider="codex")
    runner.run_coding_task("t", workspace="", model="claude-x", timeout_sec=1, provider="codex")

    # Assert: no request -> the route's model; explicit -> kept; an Anthropic name -> the route's
    assert seen == ["gpt-5.6-sol", "o3", "gpt-5.6-sol"]
