"""Environment overrides for prompt/response logging across agent hosts."""

from __future__ import annotations

from typing import Final

PROMPT_LOG_DISABLED_ENV: Final[str] = "OPENSRE_PROMPT_LOG_DISABLED"
PROMPT_LOG_LOCAL_DISABLED_ENV: Final[str] = "OPENSRE_PROMPT_LOG_LOCAL_DISABLED"
PROMPT_LOG_PATH_ENV: Final[str] = "OPENSRE_PROMPT_LOG_PATH"
PROMPT_LOG_REDACT_ENV: Final[str] = "OPENSRE_PROMPT_LOG_REDACT"

__all__ = [
    "PROMPT_LOG_DISABLED_ENV",
    "PROMPT_LOG_LOCAL_DISABLED_ENV",
    "PROMPT_LOG_PATH_ENV",
    "PROMPT_LOG_REDACT_ENV",
]
