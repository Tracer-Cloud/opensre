"""Observe integration verifier — config presence check only."""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("observe")
def verify_observe(source: str, config: dict[str, Any]) -> dict[str, str]:
    customer_id = str(config.get("customer_id", "")).strip()
    api_token = str(config.get("api_token", "")).strip()
    if not customer_id:
        return result("observe", source, "missing", "Missing customer_id.")
    if not api_token:
        return result("observe", source, "missing", "Missing api_token.")
    base_url = str(config.get("base_url", "")).strip() or f"https://{customer_id}.observeinc.com"
    return result("observe", source, "passed", f"Configured for Observe at {base_url.rstrip('/')}.")
