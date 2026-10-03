"""Shape check for the GitHub REST responses the repair loop reads."""

from __future__ import annotations

from typing import Any


def object_response(value: Any) -> dict[str, Any]:
    """Return ``value`` when GitHub answered with a JSON object; raise ``ValueError`` otherwise."""
    if not isinstance(value, dict):
        raise ValueError("GitHub returned an unexpected response.")
    return value
