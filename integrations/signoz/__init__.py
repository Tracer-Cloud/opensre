"""Public SigNoz configuration, verification, and lazy triage capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
from integrations.signoz.triage_exports import __getattr__ as __getattr__

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
        SourceFieldError as SourceFieldError,
    )
    from integrations.signoz.triage_setup import (
        connect_source as connect_source,
    )
    from integrations.signoz.triage_setup import (
        validate_source_fields as validate_source_fields,
    )
    from integrations.signoz.triage_setup import (
        validate_urls as validate_urls,
    )


__all__ = [
    "DEFAULT_SIGNOZ_MAX_RESULTS",
    "DEFAULT_SIGNOZ_TIMEOUT_SECONDS",
    "PaymentDemo",
    "SigNozConfig",
    "SigNozValidationResult",
    "SourceFieldError",
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
    "validate_source_fields",
    "validate_urls",
    "wait_for",
]
