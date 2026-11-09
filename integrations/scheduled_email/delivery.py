"""Scheduled-delivery adapter: email a loop's report from its owner's mailbox.

An email loop names the mailbox it sends from (``loop_email_account``) and its
recipients (the destination's chat id). The mailbox is always the loop
owner's own: an SMTP account they added in the app (``smtp:<record>``) or a
Gmail account they connected through Pipedream (``gmail:<account id>``). A
scheduled tick runs outside any member's turn, so delivery binds the owner as
the acting member for the send: their personal records, and the Pipedream
proxy's ``actor``, are reachable only that way and never another member's.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any

from config.constants.organization import organization_id
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CREATED_BY_PARAM,
    LOOP_EMAIL_ACCOUNT_PARAM,
)
from infrastructure.scheduling.scheduler.loops import email_recipients
from infrastructure.scheduling.scheduler.types import ScheduledTask
from integrations.smtp.delivery import send_smtp_email
from integrations.smtp.report_email import ReportEmail, render_report_email

logger = logging.getLogger(__name__)

_GMAIL_SEND_TOOL = "gmail-send-email"
_NO_OWNER = "An email loop sends from its owner's mailbox, and this loop has no owner."
_NO_MAILBOX = "Choose the mailbox this loop sends from."
#: Reports remembered for retries (one per destination attempt in flight).
_MAX_REMEMBERED = 64


class EmailScheduledDelivery:
    """Deliver a scheduled report as an email (HTML with a plain-text part).

    A failed destination is retried whole, so the adapter remembers who
    already received each report: when an SMTP server refused only some
    recipients, the retry goes to those alone and nobody gets it twice.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._received: OrderedDict[str, set[str]] = OrderedDict()

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        recipients = email_recipients(task.chat_id)
        if not recipients:
            return False, "Missing recipient email address", ""
        key = _report_key(task, message)
        received = self._already_received(key)
        recipients = [address for address in recipients if address.lower() not in received]
        if not recipients:
            return True, "", ""
        kind, _, account = str(task.params.get(LOOP_EMAIL_ACCOUNT_PARAM) or "").partition(":")
        account = account.strip()
        if not account:
            return False, _NO_MAILBOX, ""
        owner = str(task.params.get(LOOP_CREATED_BY_PARAM) or "").strip()
        org = task.organization or organization_id() or ""
        if not owner or not org:
            return False, _NO_OWNER, ""

        email = render_report_email(message, loop_name=task.name)
        scope = StorageScope(principal=Principal.org(org), actor=Actor(id=owner))
        refused: list[str] = recipients
        with bound_storage_scope(scope):
            if kind == "smtp":
                ok, error, refused = _send_smtp(account, email, recipients)
            elif kind == "gmail":
                ok, error = _send_gmail(account, email, recipients)
                refused = [] if ok else recipients
            else:
                ok, error = False, _NO_MAILBOX
        self._remember(key, [address for address in recipients if address not in refused])
        return ok, error, ""

    def _already_received(self, key: str) -> set[str]:
        with self._lock:
            return set(self._received.get(key, set()))

    def _remember(self, key: str, delivered: list[str]) -> None:
        if not delivered:
            return
        with self._lock:
            self._received.setdefault(key, set()).update(address.lower() for address in delivered)
            self._received.move_to_end(key)
            while len(self._received) > _MAX_REMEMBERED:
                self._received.popitem(last=False)


def _report_key(task: ScheduledTask, message: str) -> str:
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
    return f"{task.id}:{digest}"


def _send_smtp(
    record: str, email: ReportEmail, recipients: list[str]
) -> tuple[bool, str, list[str]]:
    from integrations.member_integrations import load_member_records

    records = load_member_records() or []
    found = _smtp_record(records, record)
    if found is None:
        return False, "The SMTP account this loop sends from is no longer connected.", recipients
    return send_smtp_email(
        subject=email.subject,
        text=email.text,
        html=email.html,
        smtp_ctx=found,
        recipients=recipients,
    )


def _smtp_record(records: list[dict[str, Any]], record: str) -> dict[str, Any] | None:
    for item in records:
        if str(item.get("service") or "").strip().lower() != "smtp":
            continue
        if record in (str(item.get("name") or ""), str(item.get("id") or "")):
            credentials = item.get("credentials")
            return dict(credentials) if isinstance(credentials, dict) else None
    return None


def _send_gmail(account: str, email: ReportEmail, recipients: list[str]) -> tuple[bool, str]:
    from integrations.pipedream.proxy import PipedreamProxyError, call_proxy_tool

    try:
        result = call_proxy_tool(
            service="gmail",
            account_id=account,
            tool_name=_GMAIL_SEND_TOOL,
            arguments={
                "to": recipients,
                "subject": email.subject,
                "body": email.html,
                "bodyType": "html",
            },
            personal=True,
        )
    except PipedreamProxyError as exc:
        logger.warning("[scheduled-email] Gmail send failed: %s", exc)
        return False, f"Gmail could not send the report: {exc}"
    if result.get("isError"):
        detail = _tool_error_text(result)
        logger.warning("[scheduled-email] Gmail refused the send: %s", detail)
        return False, f"Gmail could not send the report: {detail}"
    return True, ""


def _tool_error_text(result: dict[str, object]) -> str:
    """The text a failed Pipedream tool call returned, kept short."""
    content = result.get("content")
    parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
    text = " ".join(" ".join(parts).split()) or "the Gmail tool reported an error"
    return text[:200]


__all__ = ["EmailScheduledDelivery"]
