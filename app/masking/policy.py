"""Masking policy — configurable via environment variables.

A ``MaskingPolicy`` decides which kinds of sensitive infrastructure
identifiers are masked and what extra regex patterns apply. It is built
fresh per investigation, so env-var changes between investigations are
picked up. No module-level singleton.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import ClassVar, Literal

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

IdentifierKind = Literal[
    "pod",
    "namespace",
    "cluster",
    "hostname",
    "account_id",
    "ip_address",
    "email",
    "service_name",
]

ALL_KINDS: tuple[IdentifierKind, ...] = (
    "pod",
    "namespace",
    "cluster",
    "hostname",
    "account_id",
    "ip_address",
    "email",
    "service_name",
)


class MaskingPolicy(StrictConfigModel):
    """Configuration that drives what gets masked before LLM calls."""

    enabled: bool = False
    kinds: tuple[IdentifierKind, ...] = ALL_KINDS
    extra_patterns: dict[str, str] = Field(default_factory=dict)

    _ENV_ENABLED: ClassVar[str] = "OPENSRE_MASK_ENABLED"
    _ENV_KINDS: ClassVar[str] = "OPENSRE_MASK_KINDS"
    _ENV_EXTRA_REGEX: ClassVar[str] = "OPENSRE_MASK_EXTRA_REGEX"

    @field_validator("kinds", mode="before")
    @classmethod
    def _coerce_kinds(cls, value: object) -> tuple[IdentifierKind, ...]:
        if value is None or value == "":
            return ALL_KINDS
        if isinstance(value, str):
            parts = tuple(p.strip() for p in value.split(",") if p.strip())
            return cls._filter_valid_kinds(parts)
        if isinstance(value, list | tuple):
            parts = tuple(str(p).strip() for p in value if str(p).strip())
            return cls._filter_valid_kinds(parts)
        raise ValueError(f"kinds must be a string or list, got {type(value).__name__}")

    @classmethod
    def _filter_valid_kinds(cls, parts: tuple[str, ...]) -> tuple[IdentifierKind, ...]:
        valid: list[IdentifierKind] = []
        for p in parts:
            if p in ALL_KINDS:
                valid.append(p)  # type: ignore[arg-type]
            else:
                logger.warning("[masking] ignoring unknown identifier kind: %r", p)
        return tuple(valid) if valid else ALL_KINDS

    @field_validator("extra_patterns")
    @classmethod
    def _validate_extra_patterns(cls, value: dict[str, str]) -> dict[str, str]:
        for label, pattern in value.items():
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"extra_patterns[{label!r}] is not a valid regex: {exc}"
                ) from exc
        return value

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> MaskingPolicy:
        """Build a policy from the current environment (or an injected dict)."""
        source = env if env is not None else os.environ
        enabled = _parse_bool(source.get(cls._ENV_ENABLED, ""))
        kinds_raw = source.get(cls._ENV_KINDS, "") or ""
        extra_raw = source.get(cls._ENV_EXTRA_REGEX, "") or ""

        extra_patterns: dict[str, str] = {}
        if extra_raw.strip():
            try:
                parsed = json.loads(extra_raw)
                if isinstance(parsed, dict):
                    extra_patterns = {str(k): str(v) for k, v in parsed.items()}
                else:
                    logger.warning(
                        "[masking] %s must be a JSON object, got %s; ignoring",
                        cls._ENV_EXTRA_REGEX,
                        type(parsed).__name__,
                    )
            except json.JSONDecodeError as exc:
                logger.warning(
                    "[masking] failed to parse %s as JSON: %s; ignoring",
                    cls._ENV_EXTRA_REGEX,
                    exc,
                )

        return cls.model_validate(
            {
                "enabled": enabled,
                "kinds": kinds_raw,
                "extra_patterns": extra_patterns,
            }
        )

    def is_kind_enabled(self, kind: IdentifierKind) -> bool:
        return self.enabled and kind in self.kinds


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def compile_extra_patterns(policy: MaskingPolicy) -> dict[str, re.Pattern[str]]:
    """Compile a policy's extra regex patterns into a label→Pattern dict.

    Public helper so callers (e.g. MaskingContext) can compile once per
    investigation rather than on every mask call.
    """
    compiled: dict[str, re.Pattern[str]] = {}
    for label, pattern in policy.extra_patterns.items():
        try:
            compiled[label] = re.compile(pattern)
        except re.error as exc:
            logger.warning(
                "[masking] skipping extra pattern %r (invalid regex): %s", label, exc
            )
    return compiled


__all__ = [
    "ALL_KINDS",
    "IdentifierKind",
    "MaskingPolicy",
    "compile_extra_patterns",
]
