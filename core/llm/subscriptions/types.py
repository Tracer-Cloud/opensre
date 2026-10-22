"""Request-scoped subscription authorization."""

from __future__ import annotations

from dataclasses import dataclass, field


class SubscriptionLoginRequired(RuntimeError):
    """An explicit subscription login is needed before inference can run."""


@dataclass(frozen=True)
class SubscriptionAuth:
    """Credentials and endpoint resolved for one request, never a fallback API key."""

    token: str = field(repr=False)
    base_url: str
