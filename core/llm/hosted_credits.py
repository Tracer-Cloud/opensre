"""Admit hosted OpenSRE LLM work only when the signed-in ledger has credits.

The webapp proxy is the authority and still returns HTTP 402 on a shortfall.
This check fails closed *before* the request when the CLI can read a verified
zero, and fails open when the ledger cannot be read — never inventing empty.
"""

from __future__ import annotations

from config.account import account_llm_route
from config.account_credits import (
    cached_hosted_credits,
    fetch_hosted_credits,
    usage_page_url,
)
from core.llm.shared.llm_retry import (
    CREDIT_EXHAUSTED_MARKER,
    OpenSRECreditsExhaustedError,
)


def prefetch_hosted_credits() -> None:
    """Refresh the in-process ledger cache when the hosted route is active."""
    if account_llm_route() is None:
        return
    fetch_hosted_credits()


def hosted_credits_prompt_lines() -> tuple[str, ...]:
    """Quotable remaining-balance facts for the action/assistant prompt, or empty.

    Reads the turn's prefetched cache only — never a new HTTP call — so unit
    tests and prompt assembly stay offline.
    """
    if account_llm_route() is None:
        return ()
    read = cached_hosted_credits()
    if read is None:
        return ()
    if read.credits is None:
        return (
            "OpenSRE hosted credits remaining are unknown because the ledger "
            "could not be read — do not invent a number; `/credits` inspects it",
        )
    remaining = read.credits.total
    admission = (
        "hosted LLM requests are allowed"
        if remaining > 0
        else (
            "hosted LLM requests must not run; tell the user to top up with "
            "`/account usage` or switch provider with `/model`"
        )
    )
    return (
        f"OpenSRE hosted credits remaining are {remaining:,}",
        admission,
    )


def admit_hosted_credits() -> None:
    """Raise when the hosted route is active and the verified ledger is empty."""
    if account_llm_route() is None:
        return
    read = fetch_hosted_credits()
    if read.credits is None or read.credits.total > 0:
        return
    upgrade_url = read.usage_url or usage_page_url()
    raise OpenSRECreditsExhaustedError(
        f"OpenSRE {CREDIT_EXHAUSTED_MARKER}. Your hosted credits are exhausted. "
        f"Upgrade or top up at {upgrade_url}.",
        upgrade_url=upgrade_url,
    )


__all__ = [
    "admit_hosted_credits",
    "hosted_credits_prompt_lines",
    "prefetch_hosted_credits",
]
