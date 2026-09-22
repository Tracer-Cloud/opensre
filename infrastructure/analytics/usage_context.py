"""Org/surface/session/user context stamped onto analytics events.

This is the CLI/gateway half of org-level product analytics:

- ``organization_id`` + PostHog ``$groups.organization`` when the silo org is known
- ``surface``: ``cli`` | ``slack`` | ``telegram`` | ``discord`` | ``buzz`` (usage channel, not inventory)
- ``session_id``: OpenSRE agent session (not PostHog web ``$session_id``)
- ``user_id``: transport/platform user when known (best-effort)

Integration setup events inherit these stamps but remain a setup funnel — they
are not an integration inventory source of truth (that lives in the webapp).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from contextvars import ContextVar, Token
from enum import StrEnum
from typing import Final
from uuid import uuid4

from config.constants.organization import organization_id
from infrastructure.analytics.repl_context import get_cli_session_id

type JsonScalar = str | bool | int | float
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type Properties = dict[str, JsonValue]


class UsageSurface(StrEnum):
    CLI = "cli"
    SLACK = "slack"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    BUZZ = "buzz"
    #: A prompt sent to the hosted gateway from a signed-in shell.
    PROMPT = "prompt"


CANONICAL_SURFACES: Final[frozenset[str]] = frozenset(member.value for member in UsageSurface)
ORGANIZATION_GROUP_TYPE: Final[str] = "organization"

_SURFACE: ContextVar[str | None] = ContextVar("analytics_surface", default=None)
_SESSION_ID: ContextVar[str | None] = ContextVar("analytics_session_id", default=None)
_USER_ID: ContextVar[str | None] = ContextVar("analytics_user_id", default=None)
_ORGANIZATION_ID: ContextVar[str | None] = ContextVar("analytics_organization_id", default=None)

# Process-scoped fallback for one-shot CLI workloads
# that never enter a REPL session. Bound ContextVar / REPL session_id always win.
_PROCESS_SESSION_ID: str | None = None
_PROCESS_SESSION_ID_LOCK = threading.Lock()


def ensure_process_session_id() -> str:
    """Return a stable session id for this process, minting one on first use."""
    global _PROCESS_SESSION_ID
    if _PROCESS_SESSION_ID is None:
        with _PROCESS_SESSION_ID_LOCK:
            if _PROCESS_SESSION_ID is None:
                _PROCESS_SESSION_ID = str(uuid4())
    return _PROCESS_SESSION_ID


def get_surface() -> str | None:
    """Return the bound usage surface, if any."""
    return _SURFACE.get()


def get_session_id() -> str | None:
    """Return OpenSRE session id: bound → REPL → process fallback."""
    return _SESSION_ID.get() or get_cli_session_id() or _PROCESS_SESSION_ID


def get_user_id() -> str | None:
    """Return the bound platform/user id, if any."""
    return _USER_ID.get()


def get_organization_id() -> str | None:
    """Return org id from context, else ``ORGANIZATION_ID`` when set."""
    bound = _ORGANIZATION_ID.get()
    if bound:
        return bound
    return organization_id() or None


def bind_surface(surface: str | None) -> Token[str | None]:
    return _SURFACE.set(surface)


def bind_session_id(session_id: str | None) -> Token[str | None]:
    return _SESSION_ID.set(session_id)


def bind_user_id(user_id: str | None) -> Token[str | None]:
    return _USER_ID.set(user_id)


def bind_organization_id(organization_id: str | None) -> Token[str | None]:
    return _ORGANIZATION_ID.set(organization_id)


@contextlib.contextmanager
def bound_usage_context(
    *,
    surface: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    organization_id: str | None = None,
) -> Iterator[None]:
    """Bind usage analytics context for one CLI process scope or gateway turn."""
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    if surface is not None:
        tokens.append((_SURFACE, bind_surface(surface)))
    if session_id is not None:
        tokens.append((_SESSION_ID, bind_session_id(session_id)))
    if user_id is not None:
        tokens.append((_USER_ID, bind_user_id(user_id)))
    if organization_id is not None:
        tokens.append((_ORGANIZATION_ID, bind_organization_id(organization_id)))
    try:
        yield
    finally:
        for context_var, token in reversed(tokens):
            context_var.reset(token)


def build_usage_enrichment() -> Properties:
    """Properties to merge into captures when not already set by the caller."""
    props: Properties = {}
    organization_id = get_organization_id()
    if organization_id:
        props["organization_id"] = organization_id
        props["$groups"] = {ORGANIZATION_GROUP_TYPE: organization_id}
    surface = get_surface()
    if surface:
        props["surface"] = surface
    session_id = get_session_id()
    if session_id:
        props["session_id"] = session_id
    user_id = get_user_id()
    if user_id:
        props["user_id"] = user_id
    return props


def merge_usage_enrichment(properties: Properties) -> Properties:
    """Fill missing usage keys; caller-provided values win."""
    enrichment = build_usage_enrichment()
    merged = dict(properties)
    for key, value in enrichment.items():
        if key == "$groups":
            continue
        if key not in merged:
            merged[key] = value

    org = merged.get("organization_id")
    if isinstance(org, str) and org.strip():
        existing_groups = merged.get("$groups")
        groups = dict(existing_groups) if isinstance(existing_groups, dict) else {}
        groups[ORGANIZATION_GROUP_TYPE] = org.strip()
        merged["$groups"] = groups
    elif "$groups" not in merged:
        enrichment_groups = enrichment.get("$groups")
        if isinstance(enrichment_groups, dict):
            merged["$groups"] = enrichment_groups
    return merged
