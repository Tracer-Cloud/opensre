"""Lazy loaders for verified integrations and LLM settings (repl slash commands)."""

from __future__ import annotations


def load_verified_integrations() -> list[dict[str, str]]:
    """Import lazily so an unconfigured store doesn't slow down every REPL turn."""
    from integrations.verify import verify_integrations

    return verify_integrations()


def configured_integration_names() -> list[str]:
    """Return configured integration service names without running verifiers."""
    from integrations.verify import resolve_effective_integrations

    return sorted(resolve_effective_integrations())


def verify_integration(service: str) -> dict[str, str] | None:
    """Verify a single integration and return its result row."""
    from integrations.verify import verify_integrations

    normalized = service.strip().lower()
    if not normalized:
        return None
    rows = verify_integrations(normalized)
    return rows[0] if rows else None


__all__ = [
    "configured_integration_names",
    "load_verified_integrations",
    "verify_integration",
]
