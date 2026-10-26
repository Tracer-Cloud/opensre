"""Single LLM factory: one provider-routing decision for every model role.

The provider/transport decision (CLI-backed vs LiteLLM vs native SDK, and which
vendor) is resolved once in :func:`resolve_llm_route` and reused by every role, so
an Azure/LiteLLM routing fix cannot drift between the tool-calling agent and the
reasoning/classification/toolcall clients.

Roles differ only in the *client family* they build: :data:`LLMRole.AGENT` builds
a tool-calling client (``tool_schemas`` / ``invoke``); the other roles build the
streaming reasoning client (``invoke`` / ``invoke_stream`` / ``with_structured_output``)
for a given model tier. ``get_llm(role)`` is the single entrypoint — callers pass an ``LLMRole``.

Public interface:

- ``get_llm(role)`` — the cached client for a role; the entrypoint every surface calls.
- ``reset_llm_clients()`` — clear the cache after a ``/model`` switch or env change.

``resolve_llm_route()`` (the routing decision) and ``build_llm_client(model_type)``
(an uncached build) are exposed for tests and benchmarks, not day-to-day callers.

This module owns routing and the public entrypoint. Constructing the concrete
client for a route lives in :mod:`core.llm.client_builders`; the client cache lives
in :mod:`core.llm.internal.client_cache`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, overload

from core.llm import client_builders
from core.llm.internal.client_cache import LLMClientCache
from core.llm.internal.client_cache_key import current_llm_client_cache_key
from core.llm.transport_mode import use_litellm_for_provider
from core.llm.types import AgentLLMClient, LLMRoute, ModelType


class LLMRole(StrEnum):
    """The model tier a caller needs, independent of provider/transport."""

    AGENT = "agent"  # tool-calling ReAct (action, gather)
    REASONING = "reasoning"  # streamed assistant answer / complex reasoning
    CLASSIFICATION = "classification"  # mid-tier classifier
    TOOLCALL = "toolcall"  # lightweight tool selection / action planning


# The non-agent roles map onto the model-tier attribute suffix used by settings.
_MODEL_TYPE_BY_ROLE: dict[LLMRole, ModelType] = {
    LLMRole.REASONING: ModelType.REASONING,
    LLMRole.CLASSIFICATION: ModelType.CLASSIFICATION,
    LLMRole.TOOLCALL: ModelType.TOOLCALL,
}


def resolve_llm_route() -> LLMRoute:
    """Resolve settings + runtime provider + transport once (the single routing decision)."""
    from config.account import account_llm_route
    from config.llm_settings import PROVIDER_OPENAI

    account_route = account_llm_route()
    settings = _resolve_settings_or_raise(
        provider_override=PROVIDER_OPENAI if account_route is not None else None
    )
    if account_route is not None:
        return LLMRoute(
            settings=settings,
            provider=PROVIDER_OPENAI,
            cli_provider_registration=None,
            use_litellm=False,
        )

    runtime_provider = settings.provider
    return LLMRoute(
        settings=settings,
        provider=runtime_provider,
        cli_provider_registration=_cli_provider_registration(runtime_provider),
        use_litellm=use_litellm_for_provider(runtime_provider),
    )


def _resolve_settings_or_raise(*, provider_override: str | None = None) -> Any:
    from pydantic import ValidationError

    from config.llm_settings import llm_settings_error_message, resolve_llm_settings

    try:
        if provider_override is not None:
            return resolve_llm_settings(provider_override=provider_override)
        return resolve_llm_settings()
    except ValidationError as exc:
        raise RuntimeError(llm_settings_error_message(exc)) from exc


def _cli_provider_registration(provider: str) -> Any:
    """CLI registry entry for *provider*, or None."""
    from infrastructure.harness_providers import cli_provider_registration

    return cli_provider_registration(provider)


# ---------------------------------------------------------------------------
# Public entrypoint (cache lives in ``core.llm.internal.client_cache``)
# ---------------------------------------------------------------------------


_cache = LLMClientCache()


@overload
def get_llm(role: Literal[LLMRole.AGENT]) -> AgentLLMClient:
    pass


@overload
def get_llm(role: LLMRole) -> Any:
    pass


def get_llm(role: LLMRole) -> Any:
    """Return the cached LLM client for *role*, building it once per config."""
    config_key = current_llm_client_cache_key()
    cached = _cache.get(role, config_key)
    if cached is not None:
        return cached

    route = resolve_llm_route()
    if role is LLMRole.AGENT:
        client = client_builders.build_agent_client(route)
    else:
        client = client_builders.build_reasoning_client(route, _MODEL_TYPE_BY_ROLE[role])
    _cache.store(role, client, config_key)
    return client


def reset_llm_clients() -> None:
    """Clear all cached role clients (tests, benchmarks, ``/model`` switch, env sync)."""
    _cache.clear()


def build_llm_client(model_type: ModelType) -> Any:
    """Build a fresh (uncached) reasoning-family client for the current config."""
    return client_builders.build_reasoning_client(resolve_llm_route(), model_type)


__all__ = [
    "LLMRole",
    "LLMRoute",
    "build_llm_client",
    "get_llm",
    "reset_llm_clients",
    "resolve_llm_route",
]
