"""SigNoz integration verifier."""

from __future__ import annotations

from integrations.signoz.config import build_signoz_config
from integrations.signoz.validation import validate_signoz_config
from integrations.verification import register_validation_verifier

verify_signoz = register_validation_verifier(
    "signoz",
    build_config=build_signoz_config,
    validate_config=validate_signoz_config,
)
