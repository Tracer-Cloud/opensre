"""WhatsApp delivery helper — sends messages via Twilio."""

from __future__ import annotations

import logging

from infrastructure.delivery.notifications.delivery_errors import extract_http_error
from infrastructure.delivery.notifications.delivery_transport import post_form
from infrastructure.delivery.notifications.redaction import redact_token

logger = logging.getLogger(__name__)

_TWILIO_BASE_URL = "https://api.twilio.com/2010-04-01/Accounts"


def post_whatsapp_message_twilio(
    to: str,
    text: str,
    account_sid: str,
    auth_token: str,
    from_number: str,
) -> tuple[bool, str, str]:
    """Send a WhatsApp message via Twilio Messaging API.

    Returns (success, error, message_id).
    """
    logger.debug("[whatsapp] post twilio message to %s", to)
    url = f"{_TWILIO_BASE_URL}/{account_sid}/Messages.json"
    twilio_to = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
    twilio_from = from_number if from_number.startswith("whatsapp:") else f"whatsapp:{from_number}"
    payload = {
        "From": twilio_from,
        "To": twilio_to,
        "Body": text,
    }
    response = post_form(
        url,
        payload,
        auth=(account_sid, auth_token),
        timeout=15.0,
    )
    if not response.ok:
        error = redact_token(response.error, auth_token)
        logger.warning("[whatsapp] twilio post exception: %s", error)
        return False, error, ""

    if response.status_code not in (200, 201):
        error_message = extract_http_error(response.data, response.status_code, response.text)
        error_message = redact_token(error_message, auth_token)
        logger.warning("[whatsapp] twilio post failed: %s", error_message)
        return False, error_message, ""

    message_id = str(response.data.get("sid") or "")
    return True, "", message_id
