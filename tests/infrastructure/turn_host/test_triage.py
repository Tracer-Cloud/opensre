"""Real embedded harness enforces the durable provider budget, including handoff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.llm.types import AgentLLMResponse, ToolCall
from infrastructure.turn_host.triage import run_triage_turn
from integrations.signoz.triage_evidence import TriageEvidenceTools
from tests.integrations.signoz.test_triage import claim_store


class LoopLLM:
    model_id = "test-triage-loop"

    def __init__(self) -> None:
        self.calls = 0
        self.schemas: list[Any] = []

    def tool_schemas(self, tools: list[Any]) -> list[Any]:
        self.schemas = [t.name for t in tools]
        return []

    def invoke(self, _messages: list[Any], **_kwargs: Any) -> AgentLLMResponse:
        self.calls += 1
        return AgentLLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id=str(self.calls),
                    name="query_signoz_traces",
                    input={"service": "payment", "limit": self.calls},
                )
            ],
            raw_content=None,
        )

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": c.id, "name": c.name} for c in tool_calls],
        }

    def build_tool_result_message(self, _calls: list[Any], results: list[Any]) -> dict[str, Any]:
        return {"role": "tool", "content": json.dumps(results, default=str)}


def test_real_harness_never_exceeds_eight_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, claim = claim_store(tmp_path)
    llm = LoopLLM()
    monkeypatch.setattr("infrastructure.turn_host.triage.default_llm_factory", lambda: llm)
    monkeypatch.setattr(
        "integrations.signoz.client.SigNozClient._query_range_post",
        lambda _self, _payload: ({"data": {"data": {"results": []}}}, None),
    )
    tools = TriageEvidenceTools(claim, store, "secret", lambda: False)
    report = run_triage_turn(claim, store, tools, lambda: False)
    assert llm.calls == 8
    assert store.show(claim.id)["investigations"][0]["model_iterations"] == 8
    assert set(llm.schemas) == {"query_signoz_logs", "query_signoz_metrics", "query_signoz_traces"}
    assert report["likely_cause"] == "Insufficient evidence"
    assert report["cost_usd"] is None
