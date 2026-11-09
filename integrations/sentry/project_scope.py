"""Apply structured Sentry project scope to resolved integration context."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from infrastructure.scheduling.scheduler.agent_runner import AgentPayload


def payload_project_slug(payload: AgentPayload) -> str:
    """Return the requested project slug from a digest payload, if any."""
    return str(payload.get("project_slug") or payload.get("project") or "").strip()


def apply_sentry_project_scope(resolved: dict[str, Any], project_slug: str) -> dict[str, Any]:
    """Return a copy of *resolved* with ``sentry.project_slug`` set for tool injection."""
    slug = project_slug.strip()
    if not slug:
        return dict(resolved)

    sentry = resolved.get("sentry")
    if not sentry:
        return dict(resolved)

    if isinstance(sentry, BaseModel):
        sentry_dict = sentry.model_dump(exclude_none=True)
    elif isinstance(sentry, dict):
        sentry_dict = dict(sentry)
    else:
        return dict(resolved)

    merged = dict(resolved)
    sentry_dict["project_slug"] = slug
    merged["sentry"] = sentry_dict
    return merged


__all__ = ["apply_sentry_project_scope", "payload_project_slug"]
