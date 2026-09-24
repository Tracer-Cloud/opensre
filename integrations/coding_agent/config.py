"""Agent-neutral configuration for the coding-agent seam.

Neutral ``CODING_*`` settings with backward-compatible ``PI_CODING_*`` fallbacks,
so the coding backend can be selected/tuned without the tool layer hard-coding a
specific agent.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from config.constants import (
    CODING_AGENT_SANDBOX_AGENT,
    CODING_AGENT_SANDBOX_ENV,
    CODING_AGENT_SANDBOX_HOST,
)
from integrations.llm_cli.timeout_utils import resolve_timeout_from_env

_DEFAULT_PROVIDER = "auto"
_DEFAULT_TIMEOUT_SEC = 600.0
_MIN_TIMEOUT_SEC = 60.0
_MAX_TIMEOUT_SEC = 1800.0


def coding_agent_provider(env: Mapping[str, str] | None = None) -> str:
    """Which coding-agent backend to use (``CODING_AGENT``; defaults to ``auto``).

    ``auto`` lets the runner pick the first installed-and-authenticated backend
    (Pi, Claude Code, Codex, or Cursor); a concrete name pins one backend.
    """
    source = env if env is not None else os.environ
    return (source.get("CODING_AGENT") or _DEFAULT_PROVIDER).strip().lower() or _DEFAULT_PROVIDER


def coding_agent_sandbox(env: Mapping[str, str] | None = None) -> str:
    """Whose sandbox guards the agent's commands: ``agent`` (default) or ``host``.

    ``host`` is for a process that already runs inside an isolated container
    which cannot create the namespaces the agent's own sandbox needs.
    """
    source = env if env is not None else os.environ
    value = (source.get(CODING_AGENT_SANDBOX_ENV) or "").strip().lower()
    if value == CODING_AGENT_SANDBOX_HOST:
        return CODING_AGENT_SANDBOX_HOST
    return CODING_AGENT_SANDBOX_AGENT


def coding_model(env: Mapping[str, str] | None = None) -> str | None:
    """Model override for the coding agent (``CODING_MODEL``, else ``PI_CODING_MODEL``)."""
    source = env if env is not None else os.environ
    return (source.get("CODING_MODEL") or source.get("PI_CODING_MODEL") or "").strip() or None


def coding_workspace(env: Mapping[str, str] | None = None) -> str:
    """Workspace the agent edits (``CODING_WORKSPACE``, else ``PI_CODING_WORKSPACE``, else cwd)."""
    source = env if env is not None else os.environ
    return (
        source.get("CODING_WORKSPACE") or source.get("PI_CODING_WORKSPACE") or ""
    ).strip() or os.getcwd()


def coding_timeout_seconds() -> float:
    """Per-run timeout (``CODING_TIMEOUT_SECONDS``, else ``PI_CODING_TIMEOUT_SECONDS``)."""
    env_key = (
        "CODING_TIMEOUT_SECONDS"
        if os.environ.get("CODING_TIMEOUT_SECONDS")
        else "PI_CODING_TIMEOUT_SECONDS"
    )
    return resolve_timeout_from_env(
        env_key=env_key,
        default=_DEFAULT_TIMEOUT_SEC,
        minimum=_MIN_TIMEOUT_SEC,
        maximum=_MAX_TIMEOUT_SEC,
    )
