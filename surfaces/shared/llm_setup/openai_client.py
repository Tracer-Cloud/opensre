"""Lazy OpenAI SDK client loader used for credential validation.

The ``openai`` import is deferred so the SDK stays optional at module load.
"""

from __future__ import annotations

from typing import Any

OpenAI: Any | None = None
OpenAIAuthError: type[Exception] | None = None


def load_openai_client() -> tuple[Any, type[Exception]]:
    """Return ``(OpenAI client class, AuthenticationError type)``, importing lazily."""
    global OpenAI, OpenAIAuthError

    if OpenAI is None or OpenAIAuthError is None:
        from openai import AuthenticationError as _OpenAIAuthError
        from openai import OpenAI as _OpenAI

        OpenAI = _OpenAI
        OpenAIAuthError = _OpenAIAuthError

    return OpenAI, OpenAIAuthError
