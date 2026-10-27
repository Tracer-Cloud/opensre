"""Non-secret connection and model selection data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Availability = Literal["available", "auth-on-use", "reconnect"]


class ModelSelectionError(ValueError):
    """An invalid or unavailable explicit selection; no settings were committed."""


@dataclass(frozen=True)
class ModelChoice:
    """Exact selectable identity, display source and prompt-safe availability."""

    provider_id: str
    model_id: str
    label: str
    source: str
    active: bool = False
    availability: Availability = "available"
    detail: str = ""

    @property
    def key(self) -> str:
        import json

        return json.dumps([self.provider_id, self.model_id])
