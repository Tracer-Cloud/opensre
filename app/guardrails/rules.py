"""Guardrail rule definitions and YAML configuration loading."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class GuardrailAction(Enum):
    """Action to take when a guardrail rule matches."""

    REDACT = "redact"
    BLOCK = "block"
    AUDIT = "audit"


@dataclass(frozen=True)
class GuardrailRule:
    """A single guardrail rule with patterns, keywords, and an action."""

    name: str
    action: GuardrailAction
    patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    keywords: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    replacement: str = ""
    enabled: bool = True


def get_default_rules_path() -> Path:
    """Return the default guardrails config path."""
    return Path.home() / ".opensre" / "guardrails.yml"


def load_rules(path: Path | None = None) -> list[GuardrailRule]:
    """Load guardrail rules from a YAML file.

    Returns an empty list if the file does not exist or is malformed.
    """
    rules_path = path or get_default_rules_path()
    if not rules_path.exists():
        return []

    try:
        raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse guardrails config at %s", rules_path)
        return []

    if not isinstance(raw, dict) or "rules" not in raw:
        logger.warning("Guardrails config missing 'rules' key at %s", rules_path)
        return []

    rules: list[GuardrailRule] = []
    for entry in raw["rules"]:
        if not isinstance(entry, dict):
            continue
        parsed = _parse_rule(entry)
        if parsed is not None:
            rules.append(parsed)

    return rules


def _parse_rule(raw: dict[str, Any]) -> GuardrailRule | None:
    """Parse a single rule entry from the YAML config.

    Returns ``None`` if the rule is invalid (logs a warning).
    """
    name = raw.get("name")
    if not name:
        logger.warning("Guardrail rule missing 'name', skipping: %s", raw)
        return None

    action_str = raw.get("action", "audit")
    try:
        action = GuardrailAction(action_str)
    except ValueError:
        logger.warning("Invalid action %r in rule %r, skipping", action_str, name)
        return None

    compiled_patterns: list[re.Pattern[str]] = []
    for pat_str in raw.get("patterns", []):
        try:
            compiled_patterns.append(re.compile(pat_str, re.IGNORECASE))
        except re.error as exc:
            logger.warning("Invalid regex %r in rule %r: %s — skipping pattern", pat_str, name, exc)

    raw_keywords = raw.get("keywords", [])
    keywords = tuple(str(kw).lower() for kw in raw_keywords)

    if not compiled_patterns and not keywords:
        logger.warning("Rule %r has no patterns or keywords, skipping", name)
        return None

    return GuardrailRule(
        name=name,
        action=action,
        patterns=tuple(compiled_patterns),
        keywords=keywords,
        description=raw.get("description", ""),
        replacement=raw.get("replacement", ""),
        enabled=raw.get("enabled", True),
    )
