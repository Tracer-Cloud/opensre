from __future__ import annotations

import httpx
import pytest
from anthropic import AuthenticationError

from app.agent.tools.clients import llm_client
from app.demo import local_rca
from app.demo.local_rca import DEFAULT_FIXTURE_PATH, load_demo_fixture, prepare_demo_state


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAnthropicResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]


class _FakeMessagesAPI:
    def __init__(self, result: object) -> None:
        self._result = result

    def create(self, **_kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeAnthropicClient:
    def __init__(self, result: object) -> None:
        self.messages = _FakeMessagesAPI(result)


def _anthropic_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _anthropic_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=_anthropic_request())


def test_load_demo_fixture_reads_bundled_alert_and_evidence() -> None:
    fixture = load_demo_fixture(DEFAULT_FIXTURE_PATH)

    assert fixture["alert"]["title"]
    assert fixture["evidence"]["datadog_logs"]


def test_prepare_demo_state_populates_alert_and_evidence_context() -> None:
    fixture = load_demo_fixture(DEFAULT_FIXTURE_PATH)

    state = prepare_demo_state(fixture)

    assert state["alert_name"] == fixture["alert"]["title"]
    assert state["pipeline_name"] == "kubernetes_etl_pipeline"
    assert state["alert_source"] == "datadog"
    assert state["evidence"] == fixture["evidence"]
    assert state["available_sources"]["datadog"]["site"] == "datadoghq.com"
    assert "Namespace: tracer-test" in state["problem_md"]


def test_run_demo_fails_when_llm_authentication_fails(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "bad-key")
    monkeypatch.setattr(llm_client, "_llm", None)
    monkeypatch.setattr(
        llm_client,
        "Anthropic",
        lambda **_kwargs: _FakeAnthropicClient(
            AuthenticationError(
                "unauthorized",
                response=_anthropic_response(401),
                body=None,
            )
        ),
    )

    with pytest.raises(RuntimeError, match="Anthropic authentication failed"):
        local_rca.run_demo(["--fixture", str(DEFAULT_FIXTURE_PATH)])

    monkeypatch.setattr(llm_client, "_llm", None)


def test_run_demo_succeeds_when_llm_authentication_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "good-key")
    monkeypatch.setattr(llm_client, "_llm", None)
    monkeypatch.setattr(
        llm_client,
        "Anthropic",
        lambda **_kwargs: _FakeAnthropicClient(
            _FakeAnthropicResponse(
                "ROOT_CAUSE: The pipeline failed because the upstream load job exhausted memory.\n"
                "ROOT_CAUSE_CATEGORY: resource_exhaustion\n"
                "VALIDATED_CLAIMS:\n"
                "- The job failed during the transform stage.\n"
                "NON_VALIDATED_CLAIMS:\n"
                "- A retry may succeed after scaling memory.\n"
                "CAUSAL_CHAIN:\n"
                "- Upstream load increased memory pressure.\n"
                "- The transform pod was OOMKilled.\n"
            )
        ),
    )

    report = local_rca.run_demo(["--fixture", str(DEFAULT_FIXTURE_PATH)])

    assert "pipeline failed because the upstream load job exhausted memory" in report.lower()
    monkeypatch.setattr(llm_client, "_llm", None)
