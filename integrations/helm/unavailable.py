"""Standard Helm unavailable tool response.

Helm tools return ``helm_base_unavailable(...)`` when ``helm_client_for_run``
returns ``None`` or the Helm binary is missing, instead of invoking the client.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.utils import tool_unavailable


def helm_base_unavailable(error: str) -> dict[str, Any]:
    """Build the standard Helm "unavailable" tool response.

    Args:
        error: Human-readable reason the Helm tool can't run right now
            (e.g. why ``helm_client_for_run`` returned ``None``).

    Returns:
        ``{"source": "helm", "available": False, "error": error}``, the
        envelope Helm tools return directly as their result instead of
        calling into a ``HelmClient``.
    """
    return tool_unavailable("helm", error)
