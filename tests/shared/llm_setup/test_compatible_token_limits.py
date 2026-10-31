from __future__ import annotations

import json
from functools import partial
from http import HTTPStatus
from typing import Any

import httpx
import litellm
import openai
import pytest

from core.llm.transports.litellm import clients
from core.llm.transports.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient
from core.llm.transports.sdk.agent_clients import OpenAIAgentClient
from core.llm.transports.sdk.llm_clients import OpenAILLMClient
from surfaces.shared.llm_setup import validation
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


@pytest.mark.parametrize(
    "mode",
    [
        "litellm-agent",
        "litellm-invoke",
        "litellm-stream",
        "sdk-agent",
        "sdk-invoke",
        "sdk-stream",
        "validation",
    ],
)
@pytest.mark.parametrize("failure", ["recover", "unrelated", "repeated"])
def test_opaque_alias_token_limit_is_consistent_and_bounded(
    monkeypatch: pytest.MonkeyPatch, mode: str, failure: str
) -> None:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "max_tokens" in body or failure != "recover":
            param = "temperature" if failure == "unrelated" else "max_tokens"
            return httpx.Response(
                HTTPStatus.BAD_REQUEST,
                json={
                    "error": {
                        "message": f"Unsupported parameter: '{param}' is not supported with this model.",
                        "type": "invalid_request_error",
                        "param": param,
                        "code": "unsupported_parameter",
                    }
                },
            )
        choice = {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
        payload = {
            "id": "chatcmpl-test",
            "created": 0,
            "model": "prod-chat",
            "object": "chat.completion",
            "choices": [choice],
        }
        if body.get("stream"):
            payload["object"] = "chat.completion.chunk"
            payload["choices"] = [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]
            return httpx.Response(
                HTTPStatus.OK,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(HTTPStatus.OK, json=payload)

    monkeypatch.setenv("CUSTOM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "https://gateway.test/v1")
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        sdk = openai.OpenAI(
            api_key="test-key", base_url="https://gateway.test/v1", http_client=http, max_retries=0
        )
        monkeypatch.setattr(clients, "completion", partial(litellm.completion, client=sdk))
        monkeypatch.setattr(
            validation,
            "load_openai_client",
            lambda: (
                partial(openai.OpenAI, http_client=http, max_retries=0),
                openai.AuthenticationError,
            ),
        )
        success = False
        try:
            if mode == "validation":
                result = validation.validate_provider_credentials(
                    provider=PROVIDER_BY_VALUE["custom-openai"],
                    api_key="test-key",
                    model="prod-chat",
                )
                success = result.ok
            elif mode.startswith("sdk"):
                options: dict[str, Any] = {
                    "model": "prod-chat",
                    "base_url": "https://gateway.test/v1",
                    "api_key_env": "CUSTOM_OPENAI_API_KEY",
                }
                if mode == "sdk-agent":
                    agent = OpenAIAgentClient(**options)
                    agent._client = sdk
                    monkeypatch.setattr(agent, "_ensure_client", lambda: None)
                    success = agent.invoke([{"role": "user", "content": "hi"}]).content == "ok"
                else:
                    llm = OpenAILLMClient(**options)
                    monkeypatch.setattr(llm, "_ensure_client", lambda: sdk)
                    success = (
                        "".join(llm.invoke_stream("hi"))
                        if mode.endswith("stream")
                        else llm.invoke("hi").content
                    ) == "ok"
            else:
                options = {
                    "litellm_model": "openai/prod-chat",
                    "api_base": "https://gateway.test/v1",
                    "api_key_env": "CUSTOM_OPENAI_API_KEY",
                }
                if mode == "litellm-agent":
                    success = (
                        LiteLLMAgentClient(**options)
                        .invoke([{"role": "user", "content": "hi"}])
                        .content
                        == "ok"
                    )
                else:
                    llm = LiteLLMLLMClient(**options)
                    success = (
                        "".join(llm.invoke_stream("hi"))
                        if mode.endswith("stream")
                        else llm.invoke("hi").content
                    ) == "ok"
        except RuntimeError:
            assert failure != "recover"
    assert success == (failure == "recover")
    assert len(bodies) == (1 if failure == "unrelated" else 2)
    assert all(body["model"] == "prod-chat" for body in bodies)
    if len(bodies) == 2:
        first, second = (dict(body) for body in bodies)
        assert first.pop("max_tokens") == second.pop("max_completion_tokens")
        assert first == second
