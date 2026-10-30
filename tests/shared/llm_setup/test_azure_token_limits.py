from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from types import SimpleNamespace
from typing import Any

import httpx
import litellm
import openai
import pytest

from core.llm.transports.litellm import clients
from core.llm.transports.litellm.routing import (
    build_litellm_agent_client,
    build_litellm_llm_client,
)
from core.llm.types import ModelType
from surfaces.shared.llm_setup import azure_validation


@dataclass
class _AzureEndpoint:
    requests: list[dict[str, Any]] = field(default_factory=list)
    urls: list[httpx.URL] = field(default_factory=list)
    accepts_legacy: bool = False
    always_reject: bool = False
    error: dict[str, str] = field(
        default_factory=lambda: {
            "message": "Unsupported parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead.",
            "type": "invalid_request_error",
            "param": "max_tokens",
            "code": "unsupported_parameter",
        }
    )

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.urls.append(request.url)
        if self.always_reject or (not self.accepts_legacy and "max_tokens" in body):
            return httpx.Response(HTTPStatus.BAD_REQUEST, json={"error": self.error})
        choice = {"index": 0, "message": {"role": "assistant", "content": "OpenSRE ready"}}
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [{**choice, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        if body.get("stream"):
            chunk = {**payload, "object": "chat.completion.chunk"}
            chunk["choices"] = [{"index": 0, "delta": choice["message"], "finish_reason": None}]
            return httpx.Response(
                HTTPStatus.OK,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(HTTPStatus.OK, json=payload)


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[_AzureEndpoint]:
    endpoint = _AzureEndpoint()
    with httpx.Client(transport=httpx.MockTransport(endpoint.handle)) as http:
        azure_client = openai.AzureOpenAI(
            api_key="test-key",
            api_version="2024-10-21",
            azure_endpoint="https://test.openai.azure.com",
            http_client=http,
            max_retries=0,
        )
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(clients, "completion", partial(litellm.completion, client=azure_client))
        monkeypatch.setattr(
            azure_validation,
            "load_openai_client",
            lambda: (
                partial(openai.OpenAI, http_client=http, max_retries=0),
                openai.AuthenticationError,
            ),
        )
        yield endpoint


def _call(mode: str, deployment: str = "prod-chat") -> str:
    if mode == "validation":
        result = azure_validation.validate_credentials(
            api_key="test-key",
            deployment=deployment,
            base_url="https://test.openai.azure.com",
            api_version="2024-10-21",
        )
        assert result.ok, result.detail
        return result.sample_response
    settings = SimpleNamespace(
        azure_openai_base_url="https://test.openai.azure.com",
        azure_openai_api_version="2024-10-21",
        azure_openai_reasoning_model=deployment,
        azure_openai_classification_model=deployment,
        azure_openai_toolcall_model=deployment,
    )
    if mode == "agent":
        agent = build_litellm_agent_client(settings, "azure-openai")
        return agent.invoke(
            [{"role": "user", "content": "hello"}],
            system="Be helpful",
            tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
        ).content
    llm = build_litellm_llm_client(settings, "azure-openai", ModelType.REASONING)
    if mode == "stream":
        return "".join(llm.invoke_stream("hello"))
    return llm.invoke("hello").content


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream", "validation"])
@pytest.mark.parametrize("deployment", ["prod-chat", "gpt54-mini", "GPT-5-4-mini"])
def test_custom_azure_deployment_retries_only_token_parameter(
    endpoint: _AzureEndpoint, mode: str, deployment: str
) -> None:
    assert _call(mode, deployment) == "OpenSRE ready"
    assert len(endpoint.requests) == 2
    first, second = (dict(body) for body in endpoint.requests)
    assert first.pop("max_tokens") == second.pop("max_completion_tokens")
    assert first == second
    assert endpoint.urls[0] == endpoint.urls[1]
    assert endpoint.urls[0].path == f"/openai/deployments/{deployment}/chat/completions"
    assert endpoint.urls[0].params["api-version"] == "2024-10-21"


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream", "validation"])
def test_legacy_azure_deployment_keeps_max_tokens(endpoint: _AzureEndpoint, mode: str) -> None:
    endpoint.accepts_legacy = True
    assert _call(mode) == "OpenSRE ready"
    assert len(endpoint.requests) == 1
    assert "max_tokens" in endpoint.requests[0]
    assert "max_completion_tokens" not in endpoint.requests[0]


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream", "validation"])
def test_known_azure_reasoning_name_already_uses_completion_tokens(
    endpoint: _AzureEndpoint, mode: str
) -> None:
    assert _call(mode, "gpt-5-mini") == "OpenSRE ready"
    assert len(endpoint.requests) == 1
    assert "max_completion_tokens" in endpoint.requests[0]
    assert "max_tokens" not in endpoint.requests[0]


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream", "validation"])
@pytest.mark.parametrize(
    "route",
    [
        "gpt5_series/prod-chat",
        "o_series/prod-chat",
        "azure/gpt5_series/prod-chat",
        "azure/o_series/prod-chat",
    ],
)
def test_explicit_azure_routes_use_the_actual_deployment(
    endpoint: _AzureEndpoint, mode: str, route: str
) -> None:
    assert _call(mode, route) == "OpenSRE ready"
    assert len(endpoint.requests) == 1
    assert endpoint.urls[0].path == "/openai/deployments/prod-chat/chat/completions"
    assert endpoint.requests[0]["model"] == "prod-chat"
    assert "max_completion_tokens" in endpoint.requests[0]


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream", "validation"])
@pytest.mark.parametrize("rejection", ["temperature", "repeated-max-tokens"])
def test_azure_parameter_correction_is_narrow_and_bounded(
    endpoint: _AzureEndpoint, mode: str, rejection: str
) -> None:
    endpoint.always_reject = True
    if rejection == "temperature":
        endpoint.error = {
            "message": "Unsupported parameter: 'temperature' is not supported with this model.",
            "type": "invalid_request_error",
            "param": "temperature",
            "code": "unsupported_parameter",
        }
    expected_error = AssertionError if mode == "validation" else RuntimeError
    with pytest.raises(expected_error, match="Unsupported parameter"):
        _call(mode)
    assert len(endpoint.requests) == (1 if rejection == "temperature" else 2)
