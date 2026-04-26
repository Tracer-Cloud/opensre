"""Shared URL helpers."""

from app.config import get_tracer_base_url


def get_investigation_url(org_slug: str | None = None, investigation_id: str | None = None) -> str:
    """Build investigation URL using the organization slug and optional investigation ID."""
    base = get_tracer_base_url()
    prefix = f"{base}/{org_slug}" if org_slug else base
    if investigation_id:
        return f"{prefix}/investigations/{investigation_id}"
    return f"{prefix}/investigations"
