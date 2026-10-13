"""SigNoz configuration model and helpers.

Provides the normalized connection-settings model, env-var loader,
credential classifier, and count/limit formatting utilities.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import Field

from config.constants.signoz import SIGNOZ_API_KEY_ENV, SIGNOZ_URL_ENV
from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure

logger = logging.getLogger(__name__)

DEFAULT_SIGNOZ_TIMEOUT_SECONDS = 10.0
DEFAULT_SIGNOZ_MAX_RESULTS = 50


class SigNozConfig(StrictConfigModel):
    """Normalized SigNoz Query API connection settings."""

    url: str = ""
    api_key: str = ""
    timeout_seconds: float = Field(default=DEFAULT_SIGNOZ_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_SIGNOZ_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.api_key)


def build_signoz_config(raw: dict[str, Any] | None) -> SigNozConfig:
    """Build a normalized SigNoz config object from env/store data."""
    return SigNozConfig.model_validate(raw or {})


def signoz_config_from_env() -> SigNozConfig | None:
    """Load a SigNoz config from env vars."""
    url = os.getenv(SIGNOZ_URL_ENV, "").strip()
    api_key = os.getenv(SIGNOZ_API_KEY_ENV, "").strip()

    if not (url and api_key):
        return None

    return build_signoz_config(
        {
            "url": url,
            "api_key": api_key,
        }
    )


def classify(credentials: dict[str, Any], record_id: str) -> tuple[SigNozConfig | None, str | None]:
    try:
        cfg = build_signoz_config(
            {
                "url": credentials.get("url", ""),
                "api_key": credentials.get("api_key", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="signoz", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "signoz"
    return None, None


def signoz_count_label(count: int, requested_limit: int) -> str:
    """Format a list count, appending "+" when the page may be truncated.

    Every SigNoz query tool (logs/metrics/traces) applies its ``limit`` as
    the query spec's own row cap (``_clamp_limit`` in
    ``integrations/signoz/client.py``) and reports ``total`` as
    ``len(<rows>)`` from that already-capped response -- there is no
    separate unbounded total. A returned count that reaches the requested
    limit may not be every matching row.
    """
    effective_limit = max(requested_limit, 1)
    return f"{count}+" if count >= effective_limit else str(count)


def signoz_effective_limit(
    output: dict[str, Any], tool_input: dict[str, Any], default: int = 50
) -> int:
    """Resolve the row cap that actually bounded a SigNoz query result.

    ``_clamp_limit`` in ``integrations/signoz/client.py`` clamps the
    caller's requested ``limit`` down to ``SigNozConfig.max_results``, a
    config-level ceiling the tool caller never sees -- comparing a count
    against the *requested* limit alone can miss truncation the config
    imposed at a lower bound. The client's own query methods echo the
    ``effective_limit`` they actually used back in ``output``; prefer that,
    and fall back to the caller's requested ``limit`` only when it's absent
    (e.g. a pluggable ``signoz_backend`` that doesn't report it).
    """
    if "effective_limit" in output:
        return int(output["effective_limit"])
    return int(tool_input.get("limit", default))


__all__ = [
    "DEFAULT_SIGNOZ_MAX_RESULTS",
    "DEFAULT_SIGNOZ_TIMEOUT_SECONDS",
    "SigNozConfig",
    "build_signoz_config",
    "classify",
    "signoz_config_from_env",
    "signoz_count_label",
    "signoz_effective_limit",
]
