"""Structured offers awaiting a bare affirmative (yes / sure / …).

Schedule confirmation must not scrape Want-me-to prose. The turn that proposes
the schedule writes a :class:`PendingScheduleOffer` onto the session; ``yes``
reads that object and becomes a literal ``/cron add …`` with no regex.

Integration-setup offers follow the same pattern via
:class:`PendingIntegrationSetupOffer`. Both offer types implement
:class:`DispatchablePendingOffer` so expand / confirm / consume share one path
(open for another offer kind without editing the orchestrator).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from config.constants.slash_commands import INTEGRATIONS_SETUP_PREFIX
from core.agent_harness.session.want_me_to import offer_from_assistant_content

# Common morning-report defaults → human cadence labels (exact cron match only).
_CADENCE_LABELS: dict[str, str] = {
    "0 8 * * 1-5": "every weekday at 8am",
    "0 9 * * 1-5": "every weekday at 9am",
    "0 7 * * 1-5": "every weekday at 7am",
}

_SCHEDULE_OFFER_MARKERS = (
    "schedule this",
    "recurring",
    "/cron",
)

# Session attribute names that hold a pending affirmative (priority order for yes).
_PENDING_OFFER_ATTRS: tuple[str, ...] = (
    "pending_schedule_offer",
    "pending_integration_setup_offer",
)


@runtime_checkable
class DispatchablePendingOffer(Protocol):
    """Structured offer that expands a bare yes into a deterministic dispatch."""

    def to_dispatch_message(self) -> str:
        """User-message form the action driver executes without an LLM."""
        raise NotImplementedError

    def matches_expanded(self, expanded: str) -> bool:
        """True when ``expanded`` is this offer's dispatch message."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PendingScheduleOffer:
    """A schedule the user has been offered and has not yet confirmed."""

    kind: str
    cron: str
    timezone: str
    provider: str
    chat_id: str = ""
    prompt: str = ""
    skill_name: str = ""
    skill_inputs: dict[str, str] = field(default_factory=dict)

    def to_slash_command(self) -> str:
        """Literal slash the action driver dispatches without an LLM round-trip."""
        args = [
            "add",
            "--kind",
            self.kind,
            "--cron",
            self.cron,
            "--tz",
            self.timezone,
            "--provider",
            self.provider,
        ]
        if self.kind == "manual_loop":
            prompt = self.prompt.strip()
            if prompt:
                args.extend(["--prompt", prompt])
        if self.kind == "recurring_skill":
            skill = self.skill_name.strip()
            if skill:
                args.extend(["--skill", skill])
            if skill == "morning-report":
                city = self.skill_inputs.get("city", "").strip()
                if city:
                    args.extend(["--city", city])
            elif skill == "github-ci-health":
                for key, flag in (
                    ("owner", "--owner"),
                    ("repo", "--repo"),
                    ("branch", "--branch"),
                    ("pr_number", "--pr"),
                ):
                    value = self.skill_inputs.get(key, "").strip()
                    if value:
                        args.extend([flag, value])
        chat = self.chat_id.strip()
        if chat:
            args.extend(["--chat-id", chat])
        # shlex.quote the parts: the five-field cron expression is one argument,
        # and the dispatcher tokenises this text before the CLI ever sees it.
        return "/cron " + " ".join(shlex.quote(arg) for arg in args)

    def to_dispatch_message(self) -> str:
        return self.to_slash_command()

    def matches_expanded(self, expanded: str) -> bool:
        return isinstance(expanded, str) and expanded.startswith("/cron ")

    def want_me_to_body(self) -> str:
        """Canonical closer body (no leading Want me to:) for the assistant to show."""
        cadence = _CADENCE_LABELS.get(self.cron.strip(), f"on cron {self.cron}")
        dest = self.provider
        chat = self.chat_id.strip()
        if chat:
            dest = f"{self.provider} ({chat})"
        label = (
            (self.skill_name.strip() or "recurring skill")
            if self.kind == "recurring_skill"
            else self.kind
        )
        return f"schedule this as a recurring {label} {cadence} to {dest}"


@dataclass(frozen=True, slots=True)
class PendingIntegrationSetupOffer:
    """Connect a missing integration the user has been offered (L0 UpgradeCTA)."""

    service_id: str

    def to_slash_command(self) -> str:
        """Literal slash dispatched without an LLM round-trip."""
        service = self.service_id.strip()
        return f"{INTEGRATIONS_SETUP_PREFIX}{shlex.quote(service)}" if service else ""

    def to_dispatch_message(self) -> str:
        return self.to_slash_command()

    def matches_expanded(self, expanded: str) -> bool:
        return isinstance(expanded, str) and expanded.startswith(INTEGRATIONS_SETUP_PREFIX)

    def want_me_to_body(self) -> str:
        return f"connect `{self.service_id}` now"


def is_schedule_only_want_me_to(assistant_text: str) -> bool:
    """True when the closer is a schedule offer (leave it for PendingScheduleOffer)."""
    offer = offer_from_assistant_content(assistant_text)
    if not offer:
        return False
    lowered = offer.lower()
    return any(marker in lowered for marker in _SCHEDULE_OFFER_MARKERS)


def _session_pending_offers(session: Any) -> list[tuple[str, DispatchablePendingOffer]]:
    """Return (attr, offer) pairs present on ``session``, in expand priority order."""
    found: list[tuple[str, DispatchablePendingOffer]] = []
    for attr in _PENDING_OFFER_ATTRS:
        offer = getattr(session, attr, None)
        if isinstance(offer, DispatchablePendingOffer):
            found.append((attr, offer))
    return found


def first_pending_offer(session: Any) -> DispatchablePendingOffer | None:
    """Highest-priority pending offer on the session, if any."""
    pairs = _session_pending_offers(session)
    return pairs[0][1] if pairs else None


def is_pending_offer_confirmation(session: Any, expanded: str) -> bool:
    """True when ``expanded`` confirms a pending offer currently on ``session``."""
    for _attr, offer in _session_pending_offers(session):
        if offer.matches_expanded(expanded):
            return True
    return False


def consume_confirmed_pending_offer(session: Any, expanded: str) -> bool:
    """Clear the pending offer that ``expanded`` confirmed. Returns True if cleared."""
    for attr, offer in _session_pending_offers(session):
        if offer.matches_expanded(expanded):
            setattr(session, attr, None)
            return True
    return False


def clear_competing_pending_offers(session: Any, *, keep_attr: str) -> None:
    """Clear every pending-offer attr except ``keep_attr`` (one affirmative at a time)."""
    for attr in _PENDING_OFFER_ATTRS:
        if attr == keep_attr:
            continue
        if hasattr(session, attr):
            setattr(session, attr, None)


def clear_unconfirmed_pending_offers(session: Any) -> None:
    """Drop every pending affirmative when the user starts a non-confirm turn.

    Prevents a stale L0 setup / schedule offer from capturing a later bare
    ``yes`` after the user has moved on.
    """
    for attr in _PENDING_OFFER_ATTRS:
        if hasattr(session, attr):
            setattr(session, attr, None)


def arm_pending_integration_setup_offer(
    session: Any,
    *,
    service_id: str,
) -> PendingIntegrationSetupOffer | None:
    """Arm ``pending_integration_setup_offer`` after an L0 UpgradeCTA."""
    service = service_id.strip()
    if not service or not hasattr(session, "pending_integration_setup_offer"):
        return None
    offer = PendingIntegrationSetupOffer(service_id=service)
    session.pending_integration_setup_offer = offer
    clear_competing_pending_offers(session, keep_attr="pending_integration_setup_offer")
    return offer


__all__ = [
    "DispatchablePendingOffer",
    "PendingIntegrationSetupOffer",
    "PendingScheduleOffer",
    "arm_pending_integration_setup_offer",
    "clear_competing_pending_offers",
    "clear_unconfirmed_pending_offers",
    "consume_confirmed_pending_offer",
    "first_pending_offer",
    "is_pending_offer_confirmation",
    "is_schedule_only_want_me_to",
]
