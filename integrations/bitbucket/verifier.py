"""Bitbucket integration verifier (lazy-imported config helpers)."""

from __future__ import annotations

from typing import Any

from integrations.verification import register_validation_verifier


def _build_bitbucket_config(raw: dict[str, Any]) -> Any:
    from integrations.bitbucket.config import build_bitbucket_config

    return build_bitbucket_config(raw)


def _validate_bitbucket_config(config: Any) -> Any:
    from integrations.bitbucket.client import validate_bitbucket_config

    return validate_bitbucket_config(config)


verify_bitbucket = register_validation_verifier(
    "bitbucket",
    build_config=_build_bitbucket_config,
    validate_config=_validate_bitbucket_config,
)
