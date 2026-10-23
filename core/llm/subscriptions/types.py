"""Request-scoped subscription authorization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from core.llm.types import AgentLLMClient, CliLLMClient


class SubscriptionLoginRequired(RuntimeError):
    """An explicit subscription login is needed before inference can run."""


@dataclass(frozen=True)
class SubscriptionAuth:
    """Credentials and endpoint resolved for one request, never a fallback API key."""

    token: str = field(repr=False)
    base_url: str


class SubscriptionAdapter(Protocol):
    """Authentication lifecycle independent of billing and inference serialization."""

    def login(
        self,
        present: Callable[[str], None],
        *,
        select_model: Callable[[list[tuple[str, str]]], str],
        profile_id: str | None = None,
        new_account: bool = False,
        credential: str | None = None,
    ) -> str:
        """Authorize, select a model, then activate an isolated account atomically."""

    def resolve(self) -> SubscriptionAuth:
        """Resolve/renew the selected credential without interaction or API-billing fallback."""

    def models(self) -> list[tuple[str, str]]:
        """Discover models using only the selected subscription credential."""

    def verify(self) -> list[tuple[str, str]]:
        """Validate the selected subscription and return its available models."""

    def logout(self) -> str:
        """Clear the selected session and report whether remote revocation succeeded."""


class SubscriptionClientFactory(Protocol):
    """Provider-owned inference adapters; authentication does not imply API compatibility."""

    def agent_client(self, model: str) -> AgentLLMClient:
        """Construct a tool client that resolves subscription auth on every request."""

    def llm_client(
        self,
        model: str,
        *,
        usage_callback: Callable[..., object] | None = None,
    ) -> CliLLMClient:
        """Construct a streaming/structured-output client without usage-billing fallback."""
