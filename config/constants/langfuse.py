"""Langfuse LLM-tracing env-var names.

Langfuse is an optional trace backend: tracing turns on only when both API
keys are present and the ``langfuse`` extra is installed. The key/URL names
match what the Langfuse SDK reads itself, so one ``.env`` serves both OpenSRE
and ``langfuse-cli``.
"""

from __future__ import annotations

from typing import Final

LANGFUSE_PUBLIC_KEY_ENV: Final[str] = "LANGFUSE_PUBLIC_KEY"
LANGFUSE_SECRET_KEY_ENV: Final[str] = "LANGFUSE_SECRET_KEY"
LANGFUSE_BASE_URL_ENV: Final[str] = "LANGFUSE_BASE_URL"
#: Legacy alias for the base URL that older Langfuse tooling still reads.
LANGFUSE_HOST_ENV: Final[str] = "LANGFUSE_HOST"
#: Kill switch for a machine whose shared ``.env`` carries Langfuse keys that
#: should not receive traces from this process.
OPENSRE_LANGFUSE_DISABLED_ENV: Final[str] = "OPENSRE_LANGFUSE_DISABLED"

LANGFUSE_DEFAULT_BASE_URL: Final[str] = "https://cloud.langfuse.com"

__all__ = [
    "LANGFUSE_BASE_URL_ENV",
    "LANGFUSE_DEFAULT_BASE_URL",
    "LANGFUSE_HOST_ENV",
    "LANGFUSE_PUBLIC_KEY_ENV",
    "LANGFUSE_SECRET_KEY_ENV",
    "OPENSRE_LANGFUSE_DISABLED_ENV",
]
