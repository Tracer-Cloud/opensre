"""Resolve whether, and where, this process should send Langfuse traces."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from config.constants.langfuse import (
    LANGFUSE_BASE_URL_ENV,
    LANGFUSE_DEFAULT_BASE_URL,
    LANGFUSE_HOST_ENV,
    LANGFUSE_PUBLIC_KEY_ENV,
    LANGFUSE_SECRET_KEY_ENV,
    OPENSRE_LANGFUSE_DISABLED_ENV,
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class LangfuseSettings:
    """Credentials and endpoint for one Langfuse project."""

    public_key: str
    secret_key: str
    base_url: str


def _clean(value: str | None) -> str:
    return (value or "").strip()


def resolve_langfuse_settings(
    environ: Mapping[str, str] | None = None,
) -> LangfuseSettings | None:
    """Settings when tracing is opted in; ``None`` when keys are missing or disabled.

    Both keys are required — a lone public key is what ``langfuse-cli`` users
    often leave behind and must not turn tracing on.
    """
    env = os.environ if environ is None else environ
    if _clean(env.get(OPENSRE_LANGFUSE_DISABLED_ENV)).lower() in _TRUTHY:
        return None
    public_key = _clean(env.get(LANGFUSE_PUBLIC_KEY_ENV))
    secret_key = _clean(env.get(LANGFUSE_SECRET_KEY_ENV))
    if not public_key or not secret_key:
        return None
    base_url = (
        _clean(env.get(LANGFUSE_BASE_URL_ENV))
        or _clean(env.get(LANGFUSE_HOST_ENV))
        or LANGFUSE_DEFAULT_BASE_URL
    ).rstrip("/")
    return LangfuseSettings(public_key=public_key, secret_key=secret_key, base_url=base_url)


__all__ = ["LangfuseSettings", "resolve_langfuse_settings"]
