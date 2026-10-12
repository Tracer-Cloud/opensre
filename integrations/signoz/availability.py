"""Backend-aware availability check for SigNoz tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations

from typing import Any


def signoz_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real SigNoz credentials are present OR a fixture backend is injected.

    Used by SigNoz tool wrappers whose ``extract_params`` can delegate to a
    mock ``signoz_backend`` for synthetic tests.
    """
    signoz = sources.get("signoz", {})
    if signoz.get("_backend"):
        return True
    return bool(signoz.get("connection_verified") and signoz.get("url") and signoz.get("api_key"))


def signoz_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract SigNoz connection params from resolved integrations.

    Credentials are resolved from the integration store or environment, so the
    LLM never needs to supply URL or API key directly.
    """
    sz: dict[str, Any] = sources.get("signoz") or {}
    raw_cfg = sz.get("config")
    cfg: dict[str, Any] = raw_cfg if isinstance(raw_cfg, dict) else sz
    integration_id = (
        cfg.get("integration_id")
        or sz.get("integration_id")
        or cfg.get("id")
        or sz.get("id")
        or ""
    )
    url = cfg.get("url") or sz.get("url") or ""
    api_key = cfg.get("api_key") or sz.get("api_key") or ""
    return {
        "url": str(url).strip(),
        "api_key": str(api_key).strip(),
        "integration_id": str(integration_id).strip(),
    }


__all__ = ["signoz_available_or_backend", "signoz_extract_params"]
