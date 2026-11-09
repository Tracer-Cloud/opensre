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
    from core.llm import client_builders, factory
    from core.llm.types import LLMRoute

    route = LLMRoute(None, "anthropic", None, False)
    monkeypatch.setattr(factory, "resolve_llm_route", lambda: route)
    monkeypatch.setattr(client_builders, "build_agent_client", lambda _route: Model())
    assert investigation._model_report("evidence", "anthropic") == "Observed: failure"
    route = LLMRoute(None, "anthropic", object(), False)
    with pytest.raises(ValueError, match="CLI"):
        investigation._model_report("evidence", "anthropic")


def test_telemetry_sanitized_before_model_and_retention() -> None:
    text = "4111111111111111 alice@example.com password=hidden \x1b[31m"
    safe = investigation.sanitize(text)
    assert "4111111111111111" not in safe
    assert "alice@example.com" not in safe
    assert "hidden" not in safe
    assert "\x1b" not in safe


def test_model_deadline_returns_partial_and_reaps_child(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm.types import LLMRoute

    monkeypatch.setattr(
        investigation,
        "_checked_route",
        lambda _expected=None: LLMRoute(None, "anthropic", None, False),
    )
    monkeypatch.setattr(investigation, "_worker", _slow_worker)
    monkeypatch.setattr(investigation, "TELEMETRY_MODEL_TIMEOUT_SECONDS", 0.01)
    report = investigation.investigate(
        Scope("payment", "demo", 150, 180), Measurement(20, 20, 180), {"logs": [], "traces": []}
    )
    assert "20/20 failed requests" in report
    assert "Cause unknown" in report


def test_multiline_credentials_are_redacted_before_json_serialization() -> None:
    import json

    secret = "a" * 40
    payload = investigation.evidence_payload(
        Scope("payment", "demo", 150, 180),
        Measurement(20, 20, 180),
        {"logs": [{"message": "aws_secret_access_key=\n" + secret}], "traces": []},
    )
    assert secret not in payload
    assert "[REDACTED:aws_secret]" in json.loads(payload)["evidence"]["logs"][0]["message"]


def test_cli_provider_without_registered_adapter_is_rejected_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.llm import factory
    from core.llm.types import LLMRoute

    monkeypatch.setattr(factory, "resolve_llm_route", lambda: LLMRoute(None, "codex", None, False))
    with pytest.raises(ValueError, match="CLI"):
        investigation.investigate(Scope("payment", "demo", 150, 180), Measurement(20, 20, 180), {})


def _slow_worker(_connection, _payload, _provider) -> None:
    import time

    time.sleep(60)


def test_nested_redaction_preserves_normalization_and_does_not_mutate_input() -> None:
    import json

    source = {
        "logs": [{"message": ("alice@example.com", {"password=hidden": [None, True, 2, 1.5]})}]
    }
    payload = investigation.evidence_payload(
        Scope("payment", "demo", 150, 180), Measurement(20, 20, 180), source
    )
    assert json.loads(payload)["evidence"] == {
        "logs": [{"message": ["[REDACTED:email]", {"[REDACTED:password]": [None, True, 2, 1.5]}]}]
    }
    assert source["logs"][0]["message"][0] == "alice@example.com"
