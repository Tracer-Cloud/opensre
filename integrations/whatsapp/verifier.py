"""WhatsApp (Twilio API) integration verifier."""

from __future__ import annotations

from typing import Any

import requests

from infrastructure.delivery.notifications.redaction import redact_token
from integrations.verification import register_verifier, result


@register_verifier("whatsapp")
def verify_whatsapp(source: str, config: dict[str, Any]) -> dict[str, str]:
    account_sid = str(config.get("account_sid", "")).strip()
    auth_token = str(config.get("auth_token", "")).strip()
    if not account_sid:
        return result("whatsapp", source, "missing", "Missing account_sid.")
    if not auth_token:
        return result("whatsapp", source, "missing", "Missing auth_token.")

    try:
        response = requests.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json",
            auth=(account_sid, auth_token),
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        safe_error = redact_token(str(exc), auth_token)
        return result("whatsapp", source, "failed", f"Twilio API check failed: {safe_error}")

    friendly_name = str(payload.get("friendly_name", "")).strip()
    return result(
        "whatsapp",
        source,
        "passed",
        f"Connected to Twilio account {friendly_name or account_sid}.",
    )
