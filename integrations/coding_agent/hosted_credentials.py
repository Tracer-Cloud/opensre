"""Hosted OpenSRE credentials for coding-agent subprocesses.

When a personal OpenSRE account is signed in, Codex can bill the hosted
ledger instead of a local OpenAI key. The account token is passed only
through the child environment — never argv or logs.
"""

from __future__ import annotations


def hosted_openai_subprocess_env() -> dict[str, str] | None:
    """Return OpenAI-compatible env for the hosted route, or ``None`` if unsigned."""
    from config.account import account_llm_route, resolve_account_token

    route = account_llm_route()
    if route is None:
        return None
    token = resolve_account_token()
    if not token:
        return None
    return {
        "OPENAI_API_KEY": token,
        "OPENAI_BASE_URL": route.base_url,
    }
