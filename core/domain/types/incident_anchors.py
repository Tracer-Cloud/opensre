"""Alert-format anchor parsers for incident window resolution.

Each parser inspects a normalised alert payload dict and returns
``(anchor_datetime, source_label)`` when it finds a timestamp it trusts.
``resolve_incident_window`` in ``incident_window.py`` calls
:func:`extract_anchor` and applies lookback/buffer semantics on top.

Format-specific parsers (Alertmanager, Datadog, PagerDuty, CloudWatch) are
NOT defined here — core stays free of vendor-specific parsing logic. They
live in the owning ``integrations/<vendor>/`` package and are wired in via
:func:`register_anchor_parser` from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Recognised source labels returned by anchor parsers -----------------------
SOURCE_STARTS_AT = "alert.startsAt"
SOURCE_FIRED_AT = "alert.firedAt"
SOURCE_ACTIVATED_AT = "alert.activatedAt"


def parse_iso8601(value: object) -> datetime | None:
    """Parse ISO-8601 timestamps. Naive input is treated as UTC.

    Returns ``None`` for empty or malformed input rather than raising.
    The pipeline must never fail because an upstream alert payload was
    slightly off-spec.

    Accepts the trailing ``Z`` shorthand that ``datetime.fromisoformat``
    rejects on Python < 3.11 and that some vendor payloads use anyway.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def coerce_alert_dict(raw_alert: Any) -> dict[str, Any]:
    """Normalise ``raw_alert`` into a dict.

    ``state["raw_alert"]`` is typed as ``str | dict``. JSON-string
    payloads (common from webhooks) are parsed; non-dict / un-parseable
    values become an empty dict. Anchor parsers always operate on a dict.
    """
    if isinstance(raw_alert, dict):
        return raw_alert
    if isinstance(raw_alert, str):
        text = raw_alert.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# ---------------------------------------------------------------------------
# Anchor parser registry
# ---------------------------------------------------------------------------
#
# Each parser inspects the raw alert payload and returns
# ``(anchor_datetime, source_label)`` if it can find a timestamp it
# trusts, else ``None``. Format-specific parsers live in
# ``integrations/<vendor>/`` and register themselves (in incident-start
# reliability order) via :func:`register_anchor_parser`, called from
# ``integrations/harness_adapters.py``. :func:`extract_anchor` tries every
# registered parser in registration order and returns the first anchor found.

AnchorParser = Callable[[dict[str, Any]], "tuple[datetime, str] | None"]

_anchor_parsers: list[AnchorParser] = []


def register_anchor_parser(parser: AnchorParser) -> None:
    """Register an alert-format anchor parser, tried in registration order."""
    _anchor_parsers.append(parser)


def clear_anchor_parsers() -> None:
    """Remove all registered anchor parsers (tests)."""
    _anchor_parsers.clear()


def extract_anchor(payload: dict[str, Any]) -> tuple[datetime, str] | None:
    """Try every registered parser. Return the first successful anchor.

    Each parser is wrapped in a try/except: a single misbehaving parser
    cannot prevent the others from running, and an upstream payload
    cannot crash the pipeline.
    """
    for parser in list(_anchor_parsers):
        try:
            result = parser(payload)
        except Exception:
            logger.debug(
                "incident_window: anchor parser %s raised",
                getattr(parser, "__name__", repr(parser)),
                exc_info=True,
            )
            continue
        if result is not None:
            return result
    return None


__all__ = [
    "AnchorParser",
    "SOURCE_ACTIVATED_AT",
    "SOURCE_FIRED_AT",
    "SOURCE_STARTS_AT",
    "clear_anchor_parsers",
    "coerce_alert_dict",
    "extract_anchor",
    "parse_iso8601",
    "register_anchor_parser",
]
