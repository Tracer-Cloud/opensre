"""Twilio SMS notification tool.

Lets the agent push a short SMS notification through a configured
Twilio integration. The tool registry exposes this tool
whenever a Twilio integration with the SMS channel enabled exists.
"""

from __future__ import annotations

from typing import Any

from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from integrations.twilio.delivery import send_twilio_sms_report


class TwilioNotifyTool(BaseTool):
    """Send a short SMS notification via the configured Twilio integration."""

    name = "twilio_notify"
    source = "twilio"
    description = (
        "Send a short SMS notification via the configured Twilio integration. "
        "Only available when a Twilio integration with the SMS channel enabled "
        "exists."
    )
    use_cases = [
        "Paging an on-call recipient with a one-line incident summary via SMS",
        "Sending a follow-up SMS when a critical-severity alert escalates",
    ]
    requires = ["twilio"]
    input_schema = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": (
                    "Recipient phone number in E.164 (e.g. +14155551234). "
                    "Defaults to the channel default_to when omitted."
                ),
            },
            "body": {
                "type": "string",
                "description": "SMS body (truncated to the SMS limit).",
            },
        },
        "required": ["body"],
    }
    outputs = {
        "sid": "Twilio Message SID for the sent SMS",
        "status": "delivery dispatch status — 'sent' or 'failed'",
        "error": "error detail when status is 'failed'",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        twilio = sources.get("twilio") or {}
        if not (twilio.get("account_sid") and twilio.get("auth_token")):
            return False
        sms = twilio.get("sms") or {}
        return bool(
            sms.get("enabled") and (sms.get("from_number") or sms.get("messaging_service_sid"))
        )

    # NOTE: extract_params is intentionally NOT overridden. Anything it returns
    # is merged into the kwargs passed to run() and recorded in tool-call
    # execution traces/logs. Twilio credentials must never travel that path, so
    # run() resolves them itself from the integration store instead.

    def run(
        self,
        body: str,
        to: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        # Resolve Twilio credentials here rather than receiving them as kwargs:
        # run() kwargs are recorded in tool-call traces/logs, so account_sid /
        # auth_token must stay out of the call signature entirely.
        from integrations.catalog import resolve_effective_integrations

        entry = resolve_effective_integrations().get("twilio") or {}
        twilio = entry.get("config") or {}
        sms = twilio.get("sms") or {}
        account_sid = str(twilio.get("account_sid") or "")
        auth_token = str(twilio.get("auth_token") or "")
        from_number = str(sms.get("from_number") or "")
        messaging_service_sid = str(sms.get("messaging_service_sid") or "")
        to = to or str(sms.get("default_to") or "")

        if not account_sid or not auth_token:
            return tool_unavailable(
                "twilio", "Twilio integration is not configured.", status="failed", sid=""
            )
        if not (from_number or messaging_service_sid):
            return {
                "source": "twilio",
                "available": True,
                "status": "failed",
                "error": "Twilio SMS channel has no from_number or messaging_service_sid.",
                "sid": "",
            }
        if not to:
            return {
                "source": "twilio",
                "available": True,
                "status": "failed",
                "error": "No recipient — pass 'to' or configure sms.default_to.",
                "sid": "",
            }

        ok, error, sid = send_twilio_sms_report(
            body,
            {
                "account_sid": account_sid,
                "auth_token": auth_token,
                "from_number": from_number,
                "messaging_service_sid": messaging_service_sid,
                "to": to,
            },
        )
        return {
            "source": "twilio",
            "available": True,
            "status": "sent" if ok else "failed",
            "error": "" if ok else error,
            "sid": sid,
        }


twilio_notify = TwilioNotifyTool()
