"""Explicit provider-boundary hooks for the runtime agent loop."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.messages import ProviderMessage, RuntimeMessage


@dataclass(frozen=True)
class ProviderRequest:
    """Provider request payload before the concrete LLM client is invoked."""

    messages: list[ProviderMessage]
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


TransformMessagesHook = Callable[[Sequence["RuntimeMessage"]], Sequence["RuntimeMessage"]]
ConvertToLlmHook = Callable[[Any, Sequence["RuntimeMessage"]], list["ProviderMessage"]]
BeforeProviderRequestHook = Callable[[ProviderRequest], ProviderRequest | None]
AfterProviderResponseHook = Callable[[ProviderRequest, Any], Any | None]
ApiKeyResolver = Callable[[str], str]


@dataclass(frozen=True)
class ProviderHooks:
    """Hooks around context conversion, credentials, and provider requests."""

    transform_messages: TransformMessagesHook | None = None
    convert_to_llm: ConvertToLlmHook | None = None
    before_provider_request: BeforeProviderRequestHook | None = None
    after_provider_response: AfterProviderResponseHook | None = None
    get_api_key: ApiKeyResolver | None = None

    def apply_transform_messages(
        self,
        messages: Sequence[RuntimeMessage],
    ) -> list[RuntimeMessage]:
        if self.transform_messages is None:
            return list(messages)
        return list(self.transform_messages(messages))

    def apply_convert_to_llm(
        self,
        llm: Any,
        messages: Sequence[RuntimeMessage],
    ) -> list[ProviderMessage]:
        if self.convert_to_llm is None:
            from core.messages import MessageMapper

            return MessageMapper(llm).to_provider_messages(messages)
        return self.convert_to_llm(llm, messages)

    def apply_before_request(self, request: ProviderRequest) -> ProviderRequest:
        if self.before_provider_request is None:
            return request
        updated = self.before_provider_request(request)
        return request if updated is None else updated

    def apply_after_response(self, request: ProviderRequest, response: Any) -> Any:
        if self.after_provider_response is None:
            return response
        updated = self.after_provider_response(request, response)
        return response if updated is None else updated


def resolve_llm_api_key(env_name: str) -> str:
    """Default runtime credential resolver."""
    from config.llm_auth.credentials import resolve_api_key_env_for_request as _resolve
    from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS

    resolved = _resolve(env_name)
    if resolved:
        return resolved
    for provider, provider_env in API_KEY_PROVIDER_ENVS.items():
        if provider_env == env_name:
            raise RuntimeError(
                f"Missing credential for LLM provider '{provider}'. Set {env_name} "
                f"or run `opensre login {provider}`."
            )
    return resolved


__all__ = [
    "ApiKeyResolver",
    "ProviderHooks",
    "ProviderRequest",
    "resolve_llm_api_key",
]
