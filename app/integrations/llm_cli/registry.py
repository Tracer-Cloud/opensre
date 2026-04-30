"""Registration table for CLI-backed LLM providers (``LLM_PROVIDER`` subprocess path)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.integrations.llm_cli.base import LLMCLIAdapter


@dataclass(frozen=True)
class CLIProviderRegistration:
    """Maps a configured ``LLM_PROVIDER`` value to adapter construction + model env."""

    adapter_factory: Callable[[], LLMCLIAdapter]
    #: Optional model override env var; unset or empty → ``None`` (CLI default / omit flag).
    model_env_key: str


def _codex_factory() -> LLMCLIAdapter:
    from app.integrations.llm_cli.codex import CodexAdapter

    return CodexAdapter()


CLI_PROVIDER_REGISTRY: dict[str, CLIProviderRegistration] = {
    "codex": CLIProviderRegistration(adapter_factory=_codex_factory, model_env_key="CODEX_MODEL"),
}


def get_cli_provider_registration(provider: str) -> CLIProviderRegistration | None:
    """Return registration for *provider* if it is a registered CLI-backed LLM."""
    return CLI_PROVIDER_REGISTRY.get(provider)
