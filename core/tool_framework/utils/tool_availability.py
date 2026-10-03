"""Shared unavailable-payload helper for agent tools.

Every tool that can't reach its backend (missing config, failed auth, no
client) returns the same base envelope shape: ``{"source", "available":
False, "error"}``, sometimes with vendor-specific extra keys layered on top.
This module gives that shape one implementation instead of each integration
reconstructing it by hand.

Downstream classifiers (e.g. evidence L0 degradation) must recognize this
envelope structurally — never by scanning observation prose for words like
``unauthorized`` or bare ``"available": false`` inside ordinary result rows.
"""

from __future__ import annotations

from typing import Any, TypeGuard


def tool_unavailable(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """Return the standard unavailable envelope for a tool that couldn't run.

    ``extra`` is merged in after the base fields so vendor-specific keys
    (e.g. default collections like ``data: []``) can override or extend them.
    """
    return {"source": source, "available": False, "error": error, **extra}


def is_tool_unavailable_envelope(payload: Any) -> TypeGuard[dict[str, Any]]:
    """True when *payload* is a tool_unavailable-shaped mapping.

    Requires boolean ``available is False`` and a non-empty string ``error``.
    Nested objects that happen to contain ``"available": false`` (e.g. a
    feature-flag row) do not qualify unless they carry the same top-level
    ``error`` field — that is the typed failure signal, not a substring.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("available") is not False:
        return False
    error = payload.get("error")
    return isinstance(error, str) and bool(error.strip())


def envelope_source_id(payload: dict[str, Any]) -> str | None:
    """Return the ``source`` field when it is a non-empty string."""
    source = payload.get("source")
    if isinstance(source, str) and source.strip():
        return source.strip()
    return None


def envelope_setup_command(payload: dict[str, Any]) -> str | None:
    """Return the ``setup_command`` the user must run before the tool can work, if named."""
    command = payload.get("setup_command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    return None
