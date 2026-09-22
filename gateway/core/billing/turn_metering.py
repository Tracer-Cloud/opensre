"""Bind one chat turn's credit request and admit it inside the shared runner."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from config.account import account_llm_route
from gateway.core.billing.credits_client import CreditsOutcome, consume_credits


class CreditMeteringUnavailableError(RuntimeError):
    """Hosted credit admission could not reach a trustworthy decision."""


@dataclass(frozen=True, slots=True)
class TurnMeteringRequest:
    """Credit request attached to the current transport turn."""

    organization_id: str
    reason: str
    #: Stable per-delivery id (Buzz ``event_id``, Telegram ``update_id``, Slack
    #: ``ts``, Discord ``message_id``). Required, not optional: the polling
    #: transports replay after a restart, and a turn that cannot name its
    #: delivery cannot be deduplicated by the ledger.
    idempotency_key: str
    on_denied: Callable[[], None]


_CURRENT_REQUEST: ContextVar[TurnMeteringRequest | None] = ContextVar(
    "gateway_turn_metering_request", default=None
)


@contextmanager
def bound_turn_metering(
    *,
    organization_id: str,
    reason: str,
    idempotency_key: str,
    on_denied: Callable[[], None],
) -> Iterator[None]:
    """Bind the credit request consumed after shared capacity admission."""
    token = _CURRENT_REQUEST.set(
        TurnMeteringRequest(
            organization_id=organization_id,
            reason=reason,
            idempotency_key=idempotency_key,
            on_denied=on_denied,
        )
    )
    try:
        yield
    finally:
        _CURRENT_REQUEST.reset(token)


def admit_metered_turn() -> bool:
    """Consume the bound turn's credit and report whether agent work may run."""
    request = _CURRENT_REQUEST.get()
    if request is None:
        raise RuntimeError("gateway turn has no bound metering request")
    if account_llm_route() is not None:
        # Every LLM call of this turn goes through the webapp's hosted routes,
        # which reserve and settle credits per token and answer 402 when the
        # organization has none. A per-turn charge on top would bill the same
        # work twice, so the token metering is the admission.
        return True
    outcome = consume_credits(
        request.organization_id,
        reason=request.reason,
        idempotency_key=request.idempotency_key,
    )
    if outcome in (CreditsOutcome.ALLOWED, CreditsOutcome.DISABLED):
        return True
    if outcome is CreditsOutcome.DENIED:
        request.on_denied()
        return False
    raise CreditMeteringUnavailableError(
        f"hosted credit metering is {outcome.value}; refusing unmetered work"
    )


__all__ = [
    "TurnMeteringRequest",
    "CreditMeteringUnavailableError",
    "admit_metered_turn",
    "bound_turn_metering",
]
