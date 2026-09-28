"""Coding-agent environment names shared by its config and the deployment image."""

from __future__ import annotations

#: Which sandbox the coding agent runs its commands in. ``agent`` (the default)
#: keeps the agent's own sandbox; ``host`` hands the agent the whole process
#: environment because the host is already an isolated container that cannot
#: create the namespaces the agent's sandbox needs (Fargate).
CODING_AGENT_SANDBOX_ENV = "CODING_AGENT_SANDBOX"
CODING_AGENT_SANDBOX_AGENT = "agent"
CODING_AGENT_SANDBOX_HOST = "host"

__all__ = [
    "CODING_AGENT_SANDBOX_AGENT",
    "CODING_AGENT_SANDBOX_ENV",
    "CODING_AGENT_SANDBOX_HOST",
]
