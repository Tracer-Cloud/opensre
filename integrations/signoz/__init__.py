"""Public SigNoz configuration, verification, and lazy triage capabilities."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from integrations.signoz.config import (
    DEFAULT_SIGNOZ_MAX_RESULTS,
    DEFAULT_SIGNOZ_TIMEOUT_SECONDS,
    SigNozConfig,
    SigNozValidationResult,
    build_signoz_config,
    classify,
    signoz_config_from_env,
    signoz_count_label,
    signoz_effective_limit,
    signoz_extract_params,
    signoz_is_available,
    validate_signoz_config,
)

if TYPE_CHECKING:
    from integrations.signoz.notifications import parse_notification as parse_notification
    from integrations.signoz.triage_demo.runtime import (
        PaymentDemo as PaymentDemo,
    )
    from integrations.signoz.triage_demo.runtime import (
        demo_root as demo_root,
    )
    from integrations.signoz.triage_demo.runtime import (
        wait_for as wait_for,
    )
    from integrations.signoz.triage_evidence import TriageEvidenceTools as TriageEvidenceTools
    from integrations.signoz.triage_setup import (
        connect_source as connect_source,
    )
    from integrations.signoz.triage_setup import (
        validate_urls as validate_urls,
    )

_TRIAGE_EXPORTS = {
    "parse_notification": "notifications",
    "TriageEvidenceTools": "triage_evidence",
    "connect_source": "triage_setup",
    "validate_urls": "triage_setup",
    "PaymentDemo": "triage_demo.runtime",
    "demo_root": "triage_demo.runtime",
    "wait_for": "triage_demo.runtime",
}


def __getattr__(name: str) -> Any:
    """Load triage capabilities on demand without importing their client cycle."""
    module = _TRIAGE_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{module}"), name)


__all__ = [
    "DEFAULT_SIGNOZ_MAX_RESULTS",
    "DEFAULT_SIGNOZ_TIMEOUT_SECONDS",
    "PaymentDemo",
    "SigNozConfig",
    "SigNozValidationResult",
    "TriageEvidenceTools",
    "build_signoz_config",
    "classify",
    "connect_source",
    "demo_root",
    "parse_notification",
    "signoz_config_from_env",
    "signoz_count_label",
    "signoz_effective_limit",
    "signoz_extract_params",
    "signoz_is_available",
    "validate_signoz_config",
    "validate_urls",
    "wait_for",
]
