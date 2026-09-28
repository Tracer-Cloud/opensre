"""Hosted OpenSRE credentials for coding-agent subprocesses.

When a personal OpenSRE account is signed in, Codex can bill the hosted
ledger instead of a local OpenAI key. The account token is passed only
through the child environment — never argv or logs.
"""

from __future__ import annotations

from config.account import hosted_openai_env


def hosted_openai_subprocess_env() -> dict[str, str] | None:
    """Return OpenAI-compatible env for the hosted route, or ``None`` if unsigned."""
    return hosted_openai_env()
