"""ChatGPT plan requests and inference client construction."""

from collections.abc import Callable
from typing import Any

from core.context_budget import strip_internal_message_markers
from core.llm.shared.openai_chat_completions import get_attr_or_item
from core.llm.shared.openai_responses import responses_input, responses_tool_specs
from core.llm.shared.usage import emit_usage
from core.llm.subscriptions.chatgpt import resolve
from core.llm.transports.litellm.responses import ResponsesAgentClient, ResponsesLLMClient


def _stream_error(event: Any) -> RuntimeError:
    response = get_attr_or_item(event, "response")
    error = get_attr_or_item(response, "error") or get_attr_or_item(event, "error") or event
    code = get_attr_or_item(error, "code")
    if code in {
        "subscription_sharing_usage_limit_exceeded",
        "subscription_sharing_usage_unavailable",
    }:
        return RuntimeError(
            "ChatGPT plan usage is unavailable or its limit was reached. Review ChatGPT Settings → Usage, then retry."
        )
    return RuntimeError(
        "ChatGPT inference failed or was incomplete. Retry the request; verify access with `opensre auth verify chatgpt`."
    )


class ChatGPTResponsesAgentClient(ResponsesAgentClient):
    """ChatGPT uses plan-specific tool declarations and encrypted reasoning replay."""

    provider_name = "ChatGPT"
    auth_error_hint = "Run `opensre auth login chatgpt`."

    def _stream_error(self, _event: Any) -> RuntimeError:
        return _stream_error(_event)

    def _request(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        auth = resolve()
        kwargs: dict[str, Any] = {
            "model": f"openai/{self.model_id}",
            "api_key": auth.token,
            "api_base": auth.base_url,
            "input": responses_input(strip_internal_message_markers(messages)),
            "store": False,
            "stream": True,
            "num_retries": 0,
            "timeout": 120,
            "include": ["reasoning.encrypted_content"],
        }
        if system:
            kwargs["instructions"] = system
        if tools:
            kwargs["input"].insert(
                0,
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": responses_tool_specs(tools),
                },
            )
        return kwargs


class ChatGPTResponsesLLMClient(ResponsesLLMClient):
    """ChatGPT reasoning over the same complete Responses stream."""

    def __init__(self, model: str, *, usage_callback: Callable[..., object] | None = None) -> None:
        super().__init__(
            ChatGPTResponsesAgentClient(model, usage_callback=usage_callback or emit_usage)
        )


def agent_client(model: str) -> ChatGPTResponsesAgentClient:
    """Construct the ChatGPT plan tool adapter."""
    return ChatGPTResponsesAgentClient(model)


def llm_client(
    model: str, *, usage_callback: Callable[..., object] | None = None
) -> ChatGPTResponsesLLMClient:
    """Construct the ChatGPT plan reasoning adapter."""
    return ChatGPTResponsesLLMClient(model, usage_callback=usage_callback)
