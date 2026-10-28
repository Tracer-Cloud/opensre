"""Shared helpers for timeout environment parsing."""

from __future__ import annotations

import os

from integrations.llm_cli.constants import (
    DEFAULT_EXEC_TIMEOUT_SEC,
    MAX_EXEC_TIMEOUT_SEC,
    MIN_EXEC_TIMEOUT_SEC,
)


def resolve_timeout_from_env(
    *,
    env_key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Resolve timeout from an env var with defaulting + clamping.

    Invalid, empty, and non-positive values fall back to ``default``.
    """
    raw = os.environ.get(env_key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return max(minimum, min(value, maximum))


def resolve_exec_timeout_from_env(env_key: str) -> float:
    """Resolve a CLI adapter's invocation timeout using the shared exec bounds."""
    return resolve_timeout_from_env(
        env_key=env_key,
        default=DEFAULT_EXEC_TIMEOUT_SEC,
        minimum=MIN_EXEC_TIMEOUT_SEC,
        maximum=MAX_EXEC_TIMEOUT_SEC,
    )
