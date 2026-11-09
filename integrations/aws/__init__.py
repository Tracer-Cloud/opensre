"""AWS integration classifier.

``AWS_SETUP`` is re-exported lazily: harness registration imports small leaves
of this package at every CLI startup, and those must not pull the
setup/verifier chain (and with it ``boto3``).
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

from integrations._validation_helpers import report_classify_failure
from integrations.aws.role_mode_gate import (
    CONFIGURE_FIRST_INSTRUCTION,
    GATE_OPTIONS,
    GATE_QUESTION,
    NO_AMBIENT_CREDENTIALS_NOTICE,
    ConfigureCredentialsFirst,
    RoleGateChoice,
    gate_role_mode,
)
from integrations.config_models import AWSIntegrationConfig

if TYPE_CHECKING:
    from integrations.aws.setup import AWS_SETUP

logger = logging.getLogger(__name__)


def __getattr__(name: str) -> object:
    """Lazily re-export ``AWS_SETUP`` from the setup submodule (PEP 562)."""
    if name == "AWS_SETUP":
        return importlib.import_module("integrations.aws.setup").AWS_SETUP
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _text(credentials: dict[str, Any], key: str, default: str = "") -> str:
    """Stored optional fields are ``None`` when unset; the config model wants ``str``."""
    value = credentials.get(key)
    return default if value is None else str(value)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[AWSIntegrationConfig | None, str | None]:
    raw: dict[str, Any] = {
        "region": _text(credentials, "region", "us-east-1"),
        "role_arn": _text(credentials, "role_arn"),
        "external_id": _text(credentials, "external_id"),
        "integration_id": record_id,
    }
    if credentials.get("access_key_id") and credentials.get("secret_access_key"):
        raw["credentials"] = {
            "access_key_id": _text(credentials, "access_key_id"),
            "secret_access_key": _text(credentials, "secret_access_key"),
            "session_token": _text(credentials, "session_token"),
        }
    try:
        return AWSIntegrationConfig.model_validate(raw), "aws"
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="aws", record_id=record_id)
        return None, None


__all__ = [
    "AWS_SETUP",
    "CONFIGURE_FIRST_INSTRUCTION",
    "ConfigureCredentialsFirst",
    "GATE_OPTIONS",
    "GATE_QUESTION",
    "NO_AMBIENT_CREDENTIALS_NOTICE",
    "RoleGateChoice",
    "_text",
    "classify",
    "gate_role_mode",
]
