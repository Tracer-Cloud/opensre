"""LLM provider and model resolution shared by agent harness surfaces."""

from __future__ import annotations

from typing import Any


def default_llm_factory() -> Any:
    """Return the default agent LLM client.

    Uses a lazy import to avoid pulling in the full LLM stack at module load time.
    """
    from core.llm.factory import LLMRole, get_llm

    return get_llm(LLMRole.AGENT)


def default_reasoning_llm_factory() -> Any:
    """Return the default reasoning LLM client."""
    from core.llm.factory import LLMRole, get_llm

    return get_llm(LLMRole.REASONING)


def default_classification_llm_factory() -> Any:
    """Return the cheap classification-tier client (session-goal judge and validator)."""
    from core.llm.factory import LLMRole, get_llm

    return get_llm(LLMRole.CLASSIFICATION)


def agent_llm_is_cli_backed() -> bool:
    """True when configured routing sends the agent LLM to a CLI subprocess backend.

    Reads the shared routing decision and constructs no client, so a caller
    picking a policy by transport does not pay for a client it may not use.
    """
    from core.llm.factory import resolve_llm_route

    return resolve_llm_route().cli_provider_registration is not None


__all__ = [
    "agent_llm_is_cli_backed",
    "default_classification_llm_factory",
    "default_llm_factory",
    "default_reasoning_llm_factory",
]
