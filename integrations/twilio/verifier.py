"""Twilio integration verifier: account auth + SMS channel readiness.

A "passed" result confirms the account credentials authenticate and the
SMS channel has a usable sender (``from_number`` or
``messaging_service_sid``). WhatsApp is verified separately via the
standalone ``whatsapp`` integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypedDict

import requests

from infrastructure.delivery.notifications.redaction import redact_token
from integrations.verification import register_validation_verifier


class TwilioFailureKind(StrEnum):
    """Why validation failed — distinct from registry ``missing`` (build-time)."""

    API_ERROR = "api_error"
    SMS_NOT_READY = "sms_not_ready"


@dataclass(frozen=True)
class TwilioValidationResult:
    """Outcome of validating Twilio credentials and SMS channel readiness."""

    ok: bool
    detail: str
    failure_kind: TwilioFailureKind | None = None


class TwilioVerifyConfig(TypedDict):
    account_sid: str
    auth_token: str
    sms: dict[str, Any]


def build_twilio_config(raw: dict[str, Any] | None) -> TwilioVerifyConfig:
    """Require account fields before probing; SMS shape comes from classify/setup upstream."""
    payload = raw or {}
    account_sid = str(payload.get("account_sid", "")).strip()
    auth_token = str(payload.get("auth_token", "")).strip()
    if not account_sid:
        raise ValueError("Missing account_sid.")
    if not auth_token:
        raise ValueError("Missing auth_token.")
    sms_raw = payload.get("sms")
    sms = dict(sms_raw) if isinstance(sms_raw, dict) else {}
    return {
        "account_sid": account_sid,
        "auth_token": auth_token,
        "sms": sms,
    }


def _sms_channel_ready(sms_cfg: dict[str, Any]) -> bool:
    return bool(sms_cfg.get("enabled")) and bool(
        str(sms_cfg.get("from_number") or "").strip()
        or str(sms_cfg.get("messaging_service_sid") or "").strip()
    )


def validate_twilio_config(config: TwilioVerifyConfig) -> TwilioValidationResult:
    """Probe the Twilio account API, then confirm the SMS channel is ready."""
    account_sid = config["account_sid"]
    auth_token = config["auth_token"]

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
        return TwilioValidationResult(
            ok=False,
            detail=f"Twilio API check failed: {safe_error}",
            failure_kind=TwilioFailureKind.API_ERROR,
        )

    friendly_name = str(payload.get("friendly_name", "")).strip() or account_sid

    if not _sms_channel_ready(config.get("sms") or {}):
        return TwilioValidationResult(
            ok=False,
            detail=(
                f"Connected to Twilio account {friendly_name} but the SMS channel "
                "is not ready. Enable SMS and set a from_number or messaging_service_sid."
            ),
            failure_kind=TwilioFailureKind.SMS_NOT_READY,
        )

    return TwilioValidationResult(
        ok=True,
        detail=f"Connected to Twilio account {friendly_name}; SMS channel ready.",
    )


verify_twilio = register_validation_verifier(
    "twilio",
    build_config=build_twilio_config,
    validate_config=validate_twilio_config,
)

__all__ = [
    "TwilioFailureKind",
    "TwilioValidationResult",
    "TwilioVerifyConfig",
    "build_twilio_config",
    "validate_twilio_config",
    "verify_twilio",
]
