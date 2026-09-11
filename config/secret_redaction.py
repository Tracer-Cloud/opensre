"""Credential patterns shared by local history and diagnostic tracing."""

from __future__ import annotations

import re

from pydantic import ConfigDict

from config.strict_config import StrictConfigModel


class RedactionRule(StrictConfigModel):
    """One named regex with its replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    name: str
    pattern: re.Pattern[str]
    replacement: str


def _build_default_rules() -> tuple[RedactionRule, ...]:
    raw: list[tuple[str, str, str]] = [
        ("aws_key", r"(?:AKIA|ASIA)[A-Z0-9]{16}", "[REDACTED:aws_key]"),
        (
            "aws_secret",
            r"(?i)aws_secret_access_key[\s=:]+[A-Za-z0-9/+=]{40}",
            "aws_secret_access_key=[REDACTED:aws_secret]",
        ),
        ("github_pat_classic", r"ghp_[A-Za-z0-9]{36}", "[REDACTED:github_pat]"),
        ("github_pat_fine", r"github_pat_[A-Za-z0-9_]{82}", "[REDACTED:github_pat]"),
        ("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{40,}", "[REDACTED:anthropic_key]"),
        ("openai_key", r"sk-(?!ant-)[A-Za-z0-9_\-]{20,}", "[REDACTED:openai_key]"),
        ("slack_token", r"xox[bopas]-[A-Za-z0-9-]{10,}", "[REDACTED:slack_token]"),
        ("stripe_key", r"sk_(?:live|test)_[A-Za-z0-9]{24,}", "[REDACTED:stripe_key]"),
        ("bearer", r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}", "Bearer [REDACTED]"),
        (
            "jwt",
            r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",
            "[REDACTED:jwt]",
        ),
        ("password_arg", r"(?i)(--password=|password=)\S+", "[REDACTED:password]"),
        (
            "private_key",
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            "[REDACTED:private_key]",
        ),
    ]
    return tuple(
        RedactionRule(name=name, pattern=re.compile(p), replacement=repl) for (name, p, repl) in raw
    )


DEFAULT_REDACTION_RULES: tuple[RedactionRule, ...] = _build_default_rules()
