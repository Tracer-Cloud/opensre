"""SigNoz integration helpers.

Provides configuration and connectivity validation for SigNoz via the
Query Range API (``SIGNOZ_URL`` + ``SIGNOZ_API_KEY``).
"""

from __future__ import annotations

from integrations.signoz.availability import (
    signoz_extract_params,
    signoz_is_available,
)
from integrations.signoz.config import (
    DEFAULT_SIGNOZ_MAX_RESULTS,
    DEFAULT_SIGNOZ_TIMEOUT_SECONDS,
    SigNozConfig,
    build_signoz_config,
    classify,
    signoz_config_from_env,
    signoz_count_label,
    signoz_effective_limit,
)
from integrations.signoz.validation import (
    SigNozValidationResult,
    validate_signoz_config,
)

__all__ = [
    "DEFAULT_SIGNOZ_MAX_RESULTS",
    "DEFAULT_SIGNOZ_TIMEOUT_SECONDS",
    "SigNozConfig",
    "SigNozValidationResult",
    "build_signoz_config",
    "classify",
    "signoz_config_from_env",
    "signoz_count_label",
    "signoz_effective_limit",
    "signoz_extract_params",
    "signoz_is_available",
    "validate_signoz_config",
]
