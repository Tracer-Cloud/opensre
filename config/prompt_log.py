"""Configuration for prompt logging across agent hosts."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.constants.paths import session_home
from config.constants.prompt_log import (
    PROMPT_LOG_DISABLED_ENV,
    PROMPT_LOG_LOCAL_DISABLED_ENV,
    PROMPT_LOG_PATH_ENV,
    PROMPT_LOG_REDACT_ENV,
)
from config.repl_config import read_prompt_log_settings

_FALSE_VALUES = {"", "0", "false", "off", "no"}
_DEFAULT_MAX_CHARS = 32_000


def _default_log_path() -> Path:
    return session_home() / "prompt_log.jsonl"


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in _FALSE_VALUES


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True, slots=True)
class PromptLogConfig:
    enabled: bool = True
    local_enabled: bool = True
    posthog_enabled: bool = True
    redact: bool = True
    max_chars: int = _DEFAULT_MAX_CHARS
    log_path: Path = field(default_factory=_default_log_path)

    @classmethod
    def load(cls) -> PromptLogConfig:
        file_conf = read_prompt_log_settings()
        disabled = os.getenv(PROMPT_LOG_DISABLED_ENV)
        local_disabled = os.getenv(PROMPT_LOG_LOCAL_DISABLED_ENV)
        redact_env = os.getenv(PROMPT_LOG_REDACT_ENV)
        path_env = os.getenv(PROMPT_LOG_PATH_ENV)

        enabled = not _coerce_bool(disabled, default=False)
        local_enabled = not _coerce_bool(local_disabled, default=False)
        posthog_enabled = _coerce_bool(file_conf.get("posthog_enabled"), default=True)
        # Default on, matching HistoryPolicy.redact — prompt/response content can
        # carry the same token shapes as typed history and additionally leaves the
        # machine via the PostHog sink, so it should not be less guarded by default
        # than command history is. See docs/interactive-shell-privacy.mdx.
        redact = _coerce_bool(
            redact_env, default=_coerce_bool(file_conf.get("redact"), default=True)
        )
        max_chars = _coerce_int(file_conf.get("max_chars"), default=_DEFAULT_MAX_CHARS)

        raw_path = path_env or file_conf.get("path")
        log_path = Path(raw_path).expanduser() if raw_path else _default_log_path()

        return cls(
            enabled=enabled,
            local_enabled=local_enabled,
            posthog_enabled=posthog_enabled,
            redact=redact,
            max_chars=max_chars,
            log_path=log_path,
        )
