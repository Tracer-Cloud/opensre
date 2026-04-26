"""Shared URL helpers."""

from app.config import get_tracer_base_url


def get_investigation_url(
    organization_slug: str | None,
    investigation_id: str | None,
) -> str | None:
    if not organization_slug or not investigation_id:
        return None

    base_url = get_tracer_base_url().rstrip("/")
    return f"{base_url}/o/{organization_slug}/investigations/{investigation_id}"
