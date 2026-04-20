"""LangGraph investigation pipeline benchmark: baseline vs split tool LLM routing.

Uses the same compiled graph as production (`build_graph`), injects synthetic Grafana
fixtures (no Tracer JWT required), and tracks token usage by instrumenting API creates.
"""

from __future__ import annotations

import copy
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.pipeline import graph as graph_pipeline
from app.services import llm_client as llm_mod
from app.state import AgentState, make_initial_state
from tests.benchmarks.toolcall_model_benchmark.pricing import estimate_run_cost_usd
from tests.synthetic.rds_postgres.run_suite import _build_resolved_integrations
from tests.synthetic.rds_postgres.scenario_loader import (
    SUITE_DIR,
    ScenarioFixture,
    load_all_scenarios,
)

__all__ = [
    "LLMCallRecord",
    "LangGraphBenchmarkRun",
    "TokenTotals",
    "configure_baseline_reasoning_for_tools",
    "configure_split_models",
    "estimate_run_cost_usd",
    "get_fixture_by_id",
    "make_investigation_state",
    "reset_llm_singletons",
    "run_langgraph_investigation_bench",
]


@dataclass
class TokenTotals:
    """Accumulated LLM token usage (all providers summed into one counter)."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LLMCallRecord:
    """One provider API completion (after retries succeed for that attempt)."""

    call_index: int
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_sec: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LangGraphBenchmarkRun:
    label: str
    wall_seconds: float
    tokens: TokenTotals
    tokens_by_model: dict[str, TokenTotals] = field(default_factory=dict)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    final_state: dict[str, Any] = field(default_factory=dict)


def reset_llm_singletons() -> None:
    """Reset LangChain/Anthropic singletons; delegates to app llm_client."""
    llm_mod.reset_llm_singletons()


def configure_split_models() -> None:
    """Production-style split: reasoning model + separate toolcall model."""
    reset_llm_singletons()
    llm_mod.get_llm_for_reasoning()
    llm_mod.get_llm_for_tools()


def configure_baseline_reasoning_for_tools() -> None:
    """Ablation: tool nodes use the same client instance as reasoning (heavy model for everything)."""
    reset_llm_singletons()
    reasoning = llm_mod.get_llm_for_reasoning()
    llm_mod._llm_for_tools = reasoning


def make_investigation_state(fixture: ScenarioFixture) -> AgentState:
    alert = fixture.alert
    labels = alert.get("commonLabels", {}) or {}
    alert_name = str(alert.get("title") or labels.get("alertname") or fixture.scenario_id)
    pipeline_name = str(labels.get("pipeline_name") or "rds-postgres-synthetic")
    severity = str(labels.get("severity") or "critical")
    return make_initial_state(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=alert,
    )


def _stub_resolve_for_fixture(fixture: ScenarioFixture) -> Callable[..., dict[str, Any]]:
    """Return a resolve_integrations node replacement that only injects mock Grafana."""

    def _stub(_state: AgentState) -> dict[str, Any]:
        resolved = _build_resolved_integrations(fixture, use_mock_grafana=True)
        assert resolved is not None
        return {"resolved_integrations": resolved}

    return _stub


@contextmanager
def _patch_resolve_integrations(stub: Callable[..., dict[str, Any]]) -> Any:
    saved = graph_pipeline.node_resolve_integrations
    graph_pipeline.node_resolve_integrations = stub  # type: ignore[assignment]
    try:
        yield
    finally:
        graph_pipeline.node_resolve_integrations = saved  # type: ignore[assignment]


def _add_usage(
    totals: TokenTotals,
    by_model: dict[str, TokenTotals],
    model_key: str,
    inp: int,
    out: int,
) -> None:
    totals.input_tokens += inp
    totals.output_tokens += out
    bucket = by_model.setdefault(model_key, TokenTotals())
    bucket.input_tokens += inp
    bucket.output_tokens += out


@contextmanager
def _track_llm_usage(
    totals: TokenTotals,
    tokens_by_model: dict[str, TokenTotals],
    llm_calls: list[LLMCallRecord],
) -> Any:
    """Count tokens and record one row per successful API completion (incl. latency)."""
    orig_anthropic = llm_mod.LLMClient.invoke
    orig_openai = llm_mod.OpenAILLMClient.invoke

    def anthropic_invoke(self: Any, prompt_or_messages: Any) -> Any:
        self._ensure_client()
        real_create = self._client.messages.create

        def wrapped_create(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            resp = real_create(*args, **kwargs)
            lat = time.perf_counter() - t0
            u = getattr(resp, "usage", None)
            if u is not None:
                inp = int(getattr(u, "input_tokens", 0) or 0)
                out = int(getattr(u, "output_tokens", 0) or 0)
                model_key = str(kwargs.get("model") or getattr(self, "_model", "") or "unknown")
                _add_usage(totals, tokens_by_model, model_key, inp, out)
                llm_calls.append(
                    LLMCallRecord(
                        call_index=len(llm_calls) + 1,
                        model_id=model_key,
                        input_tokens=inp,
                        output_tokens=out,
                        latency_sec=lat,
                    )
                )
            return resp

        self._client.messages.create = wrapped_create  # type: ignore[method-assign]
        try:
            return orig_anthropic(self, prompt_or_messages)
        finally:
            self._client.messages.create = real_create  # type: ignore[method-assign]

    def openai_invoke(self: Any, prompt_or_messages: Any) -> Any:
        self._ensure_client()
        real_create = self._client.chat.completions.create

        def wrapped_create(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            resp = real_create(*args, **kwargs)
            lat = time.perf_counter() - t0
            u = resp.usage
            if u is not None:
                inp = int(u.prompt_tokens or 0)
                out = int(u.completion_tokens or 0)
                model_key = str(kwargs.get("model") or getattr(self, "_model", "") or "unknown")
                _add_usage(totals, tokens_by_model, model_key, inp, out)
                llm_calls.append(
                    LLMCallRecord(
                        call_index=len(llm_calls) + 1,
                        model_id=model_key,
                        input_tokens=inp,
                        output_tokens=out,
                        latency_sec=lat,
                    )
                )
            return resp

        self._client.chat.completions.create = wrapped_create  # type: ignore[method-assign]
        try:
            return orig_openai(self, prompt_or_messages)
        finally:
            self._client.chat.completions.create = real_create  # type: ignore[method-assign]

    llm_mod.LLMClient.invoke = anthropic_invoke  # type: ignore[assignment]
    llm_mod.OpenAILLMClient.invoke = openai_invoke  # type: ignore[assignment]
    try:
        yield
    finally:
        llm_mod.LLMClient.invoke = orig_anthropic  # type: ignore[assignment]
        llm_mod.OpenAILLMClient.invoke = orig_openai  # type: ignore[assignment]


def run_langgraph_investigation_bench(
    fixture: ScenarioFixture,
    *,
    label: str,
    configure_llm: Callable[[], None],
) -> LangGraphBenchmarkRun:
    """Run one full LangGraph investigation for a synthetic RDS scenario; return timing + tokens."""
    stub = _stub_resolve_for_fixture(fixture)
    totals = TokenTotals()
    tokens_by_model: dict[str, TokenTotals] = {}
    llm_calls: list[LLMCallRecord] = []
    initial = make_investigation_state(fixture)
    run_id = uuid.uuid4().hex[:8]
    config: RunnableConfig = {
        "configurable": {
            "thread_id": f"bench-{run_id}",
            "run_id": run_id,
            "langgraph_auth_user": {},
        }
    }

    configure_llm()

    with _patch_resolve_integrations(stub), _track_llm_usage(totals, tokens_by_model, llm_calls):
        graph = graph_pipeline.build_graph()
        state_in = copy.deepcopy(dict(initial))
        t0 = time.perf_counter()
        out = graph.invoke(state_in, config)
        elapsed = time.perf_counter() - t0

    return LangGraphBenchmarkRun(
        label=label,
        wall_seconds=elapsed,
        tokens=totals,
        tokens_by_model=dict(tokens_by_model),
        llm_calls=list(llm_calls),
        final_state=dict(out) if isinstance(out, dict) else {},
    )


def get_fixture_by_id(scenario_id: str) -> ScenarioFixture:
    fixtures = load_all_scenarios(SUITE_DIR)
    for f in fixtures:
        if f.scenario_id == scenario_id:
            return f
    raise ValueError(
        f"Unknown scenario_id: {scenario_id!r}. Loaded {len(fixtures)} scenarios from {SUITE_DIR}."
    )
