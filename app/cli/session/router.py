"""Input router for session mode."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

RouteKind = Literal["slash", "alert", "followup", "empty"]


@dataclass(frozen=True)
class RouteResult:
    kind: RouteKind
    text: str
    payload: dict[str, Any] | None = None


def route_input(text: str) -> RouteResult:
    stripped = text.strip()
    if not stripped:
        return RouteResult(kind="empty", text=text)
    if stripped.startswith("/"):
        return RouteResult(kind="slash", text=stripped)
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return RouteResult(kind="followup", text=text)
        if isinstance(parsed, dict):
            return RouteResult(kind="alert", text=text, payload=parsed)
    return RouteResult(kind="followup", text=text)
