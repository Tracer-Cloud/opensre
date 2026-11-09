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

from typing import Any, Literal, TypeGuard

from config.constants.tool_discovery import (
    TOOL_DISCOVERY_STATE_DENIED,
    TOOL_DISCOVERY_STATE_MISSING_CONFIG,
    TOOL_DISCOVERY_STATE_MISSING_DATA,
    TOOL_DISCOVERY_STATE_NO_MATCH,
    TOOL_DISCOVERY_STATE_TRANSPORT_FAILURE,
    TOOL_FAILURE_STATE_KEY,
    TOOL_FAILURE_STATES,
)

type ToolFailureState = Literal[
    "no_match",
    "missing_config",
    "denied",
    "transport_failure",
    "missing_data",
]


def tool_unavailable(
    source: str,
    error: str,
    **extra: Any,
) -> dict[str, Any]:
    """Return the standard unavailable envelope for a tool that couldn't run.

    ``extra`` is merged in after the base fields so vendor-specific keys
    (e.g. default collections like ``data: []``) can override or extend them.
    """
    failure_state = extra.pop(TOOL_FAILURE_STATE_KEY, TOOL_DISCOVERY_STATE_MISSING_CONFIG)
    if failure_state not in TOOL_FAILURE_STATES:
        raise ValueError(f"unknown tool failure state: {failure_state}")
    return {
        "source": source,
        "available": False,
        "error": error,
        TOOL_FAILURE_STATE_KEY: failure_state,
        **extra,
    }


def tool_no_match(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """Return an envelope for a valid lookup whose query matched nothing."""
    return tool_unavailable(source, error, failure_state=TOOL_DISCOVERY_STATE_NO_MATCH, **extra)


def tool_missing_config(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """Return an envelope for a tool whose required configuration is absent."""
    return tool_unavailable(
        source, error, failure_state=TOOL_DISCOVERY_STATE_MISSING_CONFIG, **extra
    )


def tool_access_denied(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """Return an envelope for an authenticated request refused by policy or authority."""
    return tool_unavailable(source, error, failure_state=TOOL_DISCOVERY_STATE_DENIED, **extra)


def tool_transport_failure(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """Return an envelope for a backend connection or protocol failure."""
    return tool_unavailable(
        source, error, failure_state=TOOL_DISCOVERY_STATE_TRANSPORT_FAILURE, **extra
    )


def tool_missing_data(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """Return an envelope when a reachable backend has no required data."""
    return tool_unavailable(source, error, failure_state=TOOL_DISCOVERY_STATE_MISSING_DATA, **extra)


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


def envelope_failure_state(payload: dict[str, Any]) -> ToolFailureState | None:
    """Return the stable failure state from a tool envelope, when valid."""
    state = payload.get(TOOL_FAILURE_STATE_KEY)
    return state if state in TOOL_FAILURE_STATES else None


def envelope_setup_command(payload: dict[str, Any]) -> str | None:
    """Return the ``setup_command`` the user must run before the tool can work, if named."""
    command = payload.get("setup_command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    return None
