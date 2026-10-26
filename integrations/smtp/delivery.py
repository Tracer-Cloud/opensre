"""SMTP delivery helper for email notifications."""

from __future__ import annotations

import logging
import smtplib
from contextlib import suppress
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)


def _connect_client(config: dict[str, Any]) -> smtplib.SMTP:
    host = str(config.get("host") or "").strip()
    port = int(config.get("port") or 587)
    security = str(config.get("security") or "starttls").strip().lower()
    username = str(config.get("username") or "").strip()
    password = str(config.get("password") or "")

    if security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        client = smtplib.SMTP(host, port, timeout=15)
    try:
        client.ehlo()
        if security == "starttls":
            client.starttls()
            client.ehlo()
        if username and password:
            client.login(username, password)
    except Exception:
        with suppress(Exception):
            client.close()
        raise
    return client


def verify_smtp_connection(config: dict[str, Any]) -> tuple[bool, str]:
    """Validate SMTP connectivity and optional authentication."""
    try:
        client = _connect_client(config)
    except Exception as exc:  # noqa: BLE001
        return False, f"SMTP connection failed: {exc}"
    try:
        client.noop()
    except Exception as exc:  # noqa: BLE001
        return False, f"SMTP NOOP failed: {exc}"
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            client.close()
    return True, "Connected to SMTP server successfully."


def send_smtp_report(
    *,
    report: str,
    subject: str,
    smtp_ctx: dict[str, Any],
    to_address: str = "",
) -> tuple[bool, str]:
    """Send a plain-text report via SMTP."""
    recipient = to_address.strip() or str(smtp_ctx.get("default_to") or "").strip()
    from_address = str(smtp_ctx.get("from_address") or "").strip()
    if not recipient:
        return False, "Missing recipient email address"
    if not from_address:
        return False, "Missing from_address"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = recipient
    message.set_content(report)

    try:
        client = _connect_client(smtp_ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[smtp] connection failed: %s", exc)
        return False, type(exc).__name__
    try:
        client.send_message(message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[smtp] send failed: %s", exc)
        return False, type(exc).__name__
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            client.close()
    return True, ""


def send_smtp_email(
    *,
    subject: str,
    text: str,
    html: str,
    smtp_ctx: dict[str, Any],
    recipients: list[str],
) -> tuple[bool, str, list[str]]:
    """Send one multipart (plain text + HTML) email via SMTP to ``recipients``.

    Returns ``(ok, error, refused)``. A server that accepts some recipients
    and refuses others still delivers to the accepted ones: ``ok`` is False
    and ``refused`` lists the rest, so a retry can go to those alone.
    """
    to = [address.strip() for address in recipients if address.strip()]
    from_address = str(smtp_ctx.get("from_address") or "").strip()
    if not to:
        return False, "Missing recipient email address", []
    if not from_address:
        return False, "Missing from_address", []

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = ", ".join(to)
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    try:
        client = _connect_client(smtp_ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[smtp] connection failed: %s", exc)
        return False, f"Could not connect to the SMTP server ({type(exc).__name__})", to
    try:
        refused_map = client.send_message(message)
    except smtplib.SMTPRecipientsRefused:
        logger.warning("[smtp] every recipient was refused")
        return False, "The SMTP server refused every recipient", to
    except Exception as exc:  # noqa: BLE001
        logger.warning("[smtp] send failed: %s", exc)
        return False, f"The SMTP server refused the message ({type(exc).__name__})", to
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            client.close()
    refused = [address for address in to if address in (refused_map or {})]
    if refused:
        logger.warning("[smtp] %d of %d recipients refused", len(refused), len(to))
        return False, f"The SMTP server refused {', '.join(refused)}", refused
    return True, "", []
