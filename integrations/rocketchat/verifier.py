"""Rocket.Chat integration verifier — REST ``/api/v1/me`` probe."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx

from infrastructure.delivery.notifications.redaction import redact_token
from integrations.verification import register_verifier, result


def _verify_webhook(source: str, webhook_url: str) -> dict[str, str]:
    """Non-posting reachability probe for an incoming webhook.

    A GET against a valid webhook endpoint never delivers a message; a 404
    means the URL (or its embedded token) is wrong.
    """
    try:
        response = httpx.get(webhook_url, timeout=10, follow_redirects=False)
    except Exception as exc:
        safe_error = redact_token(str(exc), webhook_url)
        return result(
            "rocketchat", source, "failed", f"Rocket.Chat webhook unreachable: {safe_error}"
        )

    if response.status_code == HTTPStatus.NOT_FOUND:
        return result(
            "rocketchat",
            source,
            "failed",
            "Rocket.Chat webhook returned 404; the URL looks invalid.",
        )
    if response.status_code in {
        HTTPStatus.OK,
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.METHOD_NOT_ALLOWED,
    }:
        return result(
            "rocketchat",
            source,
            "passed",
            f"Rocket.Chat webhook endpoint reachable (HTTP {response.status_code}) "
            "using a non-posting probe.",
        )
    return result(
        "rocketchat",
        source,
        "failed",
        f"Rocket.Chat webhook probe returned unexpected HTTP {response.status_code}.",
    )


@register_verifier("rocketchat")
def verify_rocketchat(source: str, config: dict[str, Any]) -> dict[str, str]:
    server_url = str(config.get("server_url", "")).strip().rstrip("/")
    auth_token = str(config.get("auth_token", "")).strip()
    user_id = str(config.get("user_id", "")).strip()
    webhook_url = str(config.get("webhook_url", "")).strip()

    if not (auth_token and user_id and server_url):
        if webhook_url:
            return _verify_webhook(source, webhook_url)
        if not server_url:
            return result("rocketchat", source, "missing", "Missing server_url.")
        return result("rocketchat", source, "missing", "Missing auth_token or user_id.")

    try:
        response = httpx.get(
            f"{server_url}/api/v1/me",
            headers={"X-Auth-Token": auth_token, "X-User-Id": user_id},
            timeout=10,
        )
    except Exception as exc:
        safe_error = redact_token(str(exc), auth_token)
        return result("rocketchat", source, "failed", f"Rocket.Chat API check failed: {safe_error}")

    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return result(
            "rocketchat",
            source,
            "failed",
            "Rocket.Chat auth failed: auth_token or user_id is invalid or expired.",
        )
    if response.status_code != HTTPStatus.OK:
        return result(
            "rocketchat",
            source,
            "failed",
            f"Rocket.Chat API check failed: HTTP {response.status_code}.",
        )

    try:
        payload = response.json()
    except Exception:
        payload = {}
    username = str(payload.get("username", "")).strip()
    return result(
        "rocketchat",
        source,
        "passed",
        f"Connected to Rocket.Chat as @{username or 'unknown'}.",
    )
