"""Shared HTTP-post transport for outbound message-delivery helpers.

Slack, Discord, and Telegram delivery modules each issue a JSON ``POST``
to a provider endpoint, parse the response body, and return a
``(success, error, ...)`` tuple. The transport pieces of that flow —
making the request, applying a timeout, catching network exceptions, and
attempting JSON decode — are identical; only the success criteria,
authentication scheme, and error-message extraction differ per provider.

This module hosts the shared transport so each delivery module can keep
its provider-specific payload building and result interpretation while
sharing one well-tested HTTP code path.

The helper deliberately does **not** decide whether the call succeeded
at the provider level. It returns ``ok=True`` whenever the request
completed without raising; callers then inspect ``status_code`` and
``data``/``text`` to apply provider semantics (e.g. ``data["ok"]`` for
Slack, ``status_code in (200, 201)`` for Discord, ``status_code == 200``
for Telegram).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(frozen=True)
class DeliveryResponse:
    """Normalized result of a delivery POST.

    Attributes:
        ok: ``True`` iff the request completed without raising. This is a
            transport-level signal only; provider-level success requires
            inspecting ``status_code`` / ``data`` per provider semantics.
        status_code: HTTP status code from the response, or ``0`` when the
            request itself raised before a response was received.
        data: Parsed JSON body when the response was a JSON object,
            otherwise an empty dict. Never ``None``, so callers can chain
            ``.get(...)`` safely without a None-check.
        text: Raw response body, useful for fallback error extraction
            when the body is not valid JSON or is empty.
        error: String form of the exception that aborted the request.
            Empty when ``ok`` is True.
    """

    ok: bool
    status_code: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    error: str = ""


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    follow_redirects: bool = False,
) -> DeliveryResponse:
    """POST ``payload`` as JSON to ``url`` and return a normalized result.

    On request exceptions the result carries ``ok=False`` and ``error``
    set to the exception message — callers are not expected to handle
    raised errors. The transport never re-raises.

    Args:
        url: Absolute URL to post to.
        payload: JSON-serializable dict body.
        headers: Optional headers (e.g. ``Authorization``). Defaults to
            an empty dict; httpx will still set ``Content-Type`` and the
            standard headers it manages.
        timeout: Request timeout in seconds. Defaults to 15s, matching
            the pre-existing per-provider timeouts.
        follow_redirects: Whether to follow 3xx redirects. Disabled by
            default to match Slack/Discord/Telegram REST APIs (which never
            redirect on success). Slack incoming webhooks and the NextJS
            ``/api/slack`` proxy enable it.

    Returns:
        ``DeliveryResponse`` with ``ok``, ``status_code``, ``data``,
        ``text``, and ``error`` populated. JSON decode failures are
        non-fatal: ``data`` falls back to ``{}`` and ``text`` always
        carries the raw body.
    """
    try:
        response = httpx.post(
            url,
            json=payload,
            headers=headers or {},
            timeout=timeout,
            follow_redirects=follow_redirects,
        )
    except Exception as exc:  # noqa: BLE001 — transport never re-raises
        return DeliveryResponse(ok=False, error=str(exc))

    text = response.text
    data: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            data = parsed
    except Exception:  # noqa: BLE001 — non-JSON body is permitted
        pass

    return DeliveryResponse(
        ok=True,
        status_code=response.status_code,
        data=data,
        text=text,
    )
