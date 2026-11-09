"""CLI-backed LLM adapters (``integrations.llm_cli``).

Core's LLM factory and transports build CLI-backed clients without importing the
``integrations.llm_cli`` package: the adapters are assembled once at boot and
installed as an immutable :class:`CliLlmAdapters`, read through the module
functions. There is no setter — nothing rewrites them after boot.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from core.llm.types import CliLLMClient, ModelType

CliProviderRegistrationFn = Callable[[str], Any]


class BuildCliClientFn(Protocol):
    """Construct the CLI-backed client for a registered CLI adapter."""

    def __call__(
        self,
        adapter: Any,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        model_type: ModelType | None = None,
    ) -> CliLLMClient:
        """Build the CLI-backed client for *adapter*."""


FlattenCliMessagesFn = Callable[[list[dict[str, Any]]], str]


def _default_cli_provider_registration(_provider: str) -> Any:
    return None


def _cli_llm_backend_unavailable(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(
        "CLI LLM backend is not registered — call install_harness_providers() at startup."
    )


@dataclass(frozen=True)
class CliLlmAdapters:
    """The CLI-LLM adapters, installed once at boot."""

    cli_provider_registration: CliProviderRegistrationFn = _default_cli_provider_registration
    build_cli_client: BuildCliClientFn = _cli_llm_backend_unavailable
    flatten_cli_messages: FlattenCliMessagesFn = _cli_llm_backend_unavailable

    def install(self) -> None:
        """Bind these as the process-wide CLI-LLM adapters."""
        global _installed
        _installed = self


_installed: CliLlmAdapters | None = None


def cli_provider_registration(provider: str) -> Any:
    fn = _installed.cli_provider_registration if _installed is not None else None
    return fn(provider) if fn is not None else None


def build_cli_client(
    adapter: Any,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    model_type: ModelType | None = None,
) -> CliLLMClient:
    fn = _installed.build_cli_client if _installed is not None else _cli_llm_backend_unavailable
    return fn(adapter, model=model, max_tokens=max_tokens, model_type=model_type)


def flatten_cli_messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    fn = _installed.flatten_cli_messages if _installed is not None else _cli_llm_backend_unavailable
    return fn(messages)


def reset() -> None:
    """Clear the installed CLI-LLM adapters (tests)."""
    global _installed
    _installed = None


__all__ = [
    "BuildCliClientFn",
    "CliLlmAdapters",
    "CliProviderRegistrationFn",
    "FlattenCliMessagesFn",
    "build_cli_client",
    "cli_provider_registration",
    "flatten_cli_messages_to_prompt",
]
