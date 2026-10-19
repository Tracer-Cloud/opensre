"""No ambient tools, redacted telemetry, and hard cancellation of slow synthesis."""

from collections.abc import Sequence
from typing import Any

import pytest

from core.llm.types import AgentLLMResponse, SchemaDescribedTool
from core.telemetry import investigation
from core.telemetry.models import Measurement, Scope


class Model:
    model_id = "test"

    def tool_schemas(self, _tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return []

    def invoke(self, messages, *, system=None, tools=None):
        assert tools == []
        assert "untrusted telemetry" in system
        assert messages[0]["content"] == "evidence"
        return AgentLLMResponse(content="Observed: failure")


def test_synthesis_has_no_tools_and_rejects_cli_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm import factory
    from core.llm.types import LLMRoute

    route = LLMRoute(None, "test", None, False)
    monkeypatch.setattr(factory, "resolve_llm_route", lambda: route)
    monkeypatch.setattr(factory, "get_llm", lambda _role: Model())
    assert investigation._model_report("evidence") == "Observed: failure"
    route = LLMRoute(None, "test", object(), False)
    with pytest.raises(ValueError, match="CLI"):
        investigation._model_report("evidence")


def test_telemetry_sanitized_before_model_and_retention() -> None:
    text = "4111111111111111 alice@example.com password=hidden \x1b[31m"
    safe = investigation.sanitize(text)
    assert "4111111111111111" not in safe
    assert "alice@example.com" not in safe
    assert "hidden" not in safe
    assert "\x1b" not in safe


def test_model_deadline_returns_partial_and_reaps_child(monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero deadline exercises the real spawn/terminate path without making a provider request.
    monkeypatch.setattr(investigation, "TELEMETRY_MODEL_TIMEOUT_SECONDS", 0)
    report = investigation.investigate(
        Scope("payment", "demo", 150, 180), Measurement(20, 20, 180), {"logs": [], "traces": []}
    )
    assert "20/20 failed requests" in report
    assert "Cause unknown" in report
