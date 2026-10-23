"""xAI subscription inference using LiteLLM's native Responses interface."""

from collections.abc import Callable
from typing import Any

from core.context_budget import strip_internal_message_markers
from core.llm.shared.openai_responses import responses_input, responses_tool_specs
from core.llm.shared.usage import emit_usage
from core.llm.subscriptions.grok import resolve
from core.llm.transports.litellm.responses import ResponsesAgentClient, ResponsesLLMClient


class GrokResponsesAgentClient(ResponsesAgentClient):
    """Use xAI's ordinary Responses tools; never send ChatGPT plan extensions."""

    provider_name = "Grok subscription"
    auth_error_hint = (
        "Run `opensre auth verify grok`; check xAI subscription entitlement and quota."
    )

    def _request(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        from litellm.utils import supports_native_streaming

        # LiteLLM otherwise substitutes a buffered request for unknown models.
        # Subscription streams must preserve the provider's terminal event.
        if not supports_native_streaming(model=self.model_id, custom_llm_provider="xai"):
            raise RuntimeError(
                "This Grok model is not recognized for native streaming by LiteLLM. "
                "Choose a supported model or update LiteLLM before retrying."
            )
        auth = resolve()
        kwargs: dict[str, Any] = {
            "model": f"xai/{self.model_id}",
            "api_key": auth.token,
            "api_base": auth.base_url,
            "input": responses_input(strip_internal_message_markers(messages)),
            "store": False,
            "stream": True,
            "num_retries": 0,
            "timeout": 900,
            "include": ["reasoning.encrypted_content"],
        }
        if system:
            kwargs["instructions"] = system
        if tools:
            kwargs["tools"] = responses_tool_specs(tools)
        return kwargs


class GrokResponsesLLMClient(ResponsesLLMClient):
    """Grok reasoning/structured output over complete subscription Responses streams."""

    def __init__(self, model: str, *, usage_callback: Callable[..., object] | None = None) -> None:
        super().__init__(
            GrokResponsesAgentClient(model, usage_callback=usage_callback or emit_usage)
        )


def agent_client(model: str) -> GrokResponsesAgentClient:
    """Construct the xAI subscription tool adapter."""
    return GrokResponsesAgentClient(model)


def llm_client(
    model: str, *, usage_callback: Callable[..., object] | None = None
) -> GrokResponsesLLMClient:
    """Construct subscription reasoning without model or billing fallback."""
    return GrokResponsesLLMClient(model, usage_callback=usage_callback)
