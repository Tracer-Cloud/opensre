"""Request-local compatibility for OpenAI-compatible chat completion token limits."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any


def _rejects_max_tokens(error: Exception) -> bool:
    if getattr(error, "status_code", None) != HTTPStatus.BAD_REQUEST:
        return False
    body = getattr(error, "body", None)
    if isinstance(body, Mapping):
        body = body.get("error", body)
    payload = body if isinstance(body, Mapping) else {}
    param = payload.get("param", getattr(error, "param", None))
    code = payload.get("code", getattr(error, "code", None))
    if param == "max_tokens" and code == "unsupported_parameter":
        return True
    if param is not None and param != "max_tokens":
        return False
    # Some LiteLLM versions retain only Azure's error message, not its fields.
    message = str(payload.get("message") or getattr(error, "message", error))
    message = message.lower().replace('"', "'")
    return (
        "unsupported parameter: 'max_tokens'" in message
        or "'max_tokens' is not supported" in message
    )


def completion_token_retry_kwargs(
    kwargs: dict[str, Any], error: Exception
) -> dict[str, Any] | None:
    """Copy an explicitly rejected chat request, replacing only its token limit.

    Deployment names cannot identify the underlying model. Removing ``max_tokens``
    bounds this correction to once per request without shared client state.
    """
    if (
        "max_tokens" not in kwargs
        or "max_completion_tokens" in kwargs
        or not _rejects_max_tokens(error)
    ):
        return None
    retry = dict(kwargs)
    retry["max_completion_tokens"] = retry.pop("max_tokens")
    return retry


def litellm_token_retry_kwargs(kwargs: dict[str, Any], error: Exception) -> dict[str, Any] | None:
    """Apply Token-limit compatibility only to Azure or explicit compatible endpoints."""
    model = str(kwargs.get("model", ""))
    if not (model.startswith("azure/") or (model.startswith("openai/") and kwargs.get("api_base"))):
        return None
    return completion_token_retry_kwargs(kwargs, error)
