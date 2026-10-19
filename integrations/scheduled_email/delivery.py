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

import logging
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


class EmailScheduledDelivery:
    """Deliver a scheduled report as an email (HTML with a plain-text part)."""

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        recipients = email_recipients(task.chat_id)
        if not recipients:
            return False, "Missing recipient email address", ""
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
        with bound_storage_scope(scope):
            if kind == "smtp":
                ok, error = _send_smtp(account, email, recipients)
            elif kind == "gmail":
                ok, error = _send_gmail(account, email, recipients)
            else:
                ok, error = False, _NO_MAILBOX
        return ok, error, ""


def _send_smtp(record: str, email: ReportEmail, recipients: list[str]) -> tuple[bool, str]:
    from integrations.member_integrations import load_member_records

    records = load_member_records() or []
    found = _smtp_record(records, record)
    if found is None:
        return False, "The SMTP account this loop sends from is no longer connected."
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
        call_proxy_tool(
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
    return True, ""


__all__ = ["EmailScheduledDelivery"]
