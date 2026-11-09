"""Shared HTTP error extraction for outbound notification delivery helpers.

Slack and Discord delivery modules both receive a ``DeliveryResponse`` from
``delivery_transport.post_json`` and need a human-readable error string when
the provider rejects the request. The fallback chain — JSON error fields, then
raw response body, then HTTP status — is identical; only the JSON key names
differ per provider (``message`` for Discord, ``error`` for Slack).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_ERROR_TEXT_TRUNCATE = 500


def extract_http_error(
    data: Mapping[str, Any],
    status_code: int,
    text: str,
) -> str:
    """Return a human-readable error string from an HTTP API response.

    Tries ``data["message"]`` and ``data["error"]`` first, then falls back to
    the raw response body or the HTTP status code so non-JSON failure bodies
    (HTML, plain text) never cause a crash.
    """
    msg = data.get("message")
    if msg:
        return str(msg)
    err_msg = data.get("error_message")
    if err_msg:
        return str(err_msg)
    err = data.get("error")
    if err:
        return str(err)
    if text:
        return text[:_ERROR_TEXT_TRUNCATE]
    return f"HTTP {status_code}"
