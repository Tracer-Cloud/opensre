"""Load the bundled master proactive-message judgement policy."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_MASTER_POLICY_FILENAME = "MASTER_JUDGEMENT.md"
_SUPPORTED_TRIGGER = "after_slack_turn"
_SUPPORTED_SCHEDULE = "event_driven"
_MAX_SLACK_CONTEXT_MESSAGES = 100


@dataclass(frozen=True, slots=True)
class ProactiveMessagePolicy:
    """Trusted policy text plus the small amount of host scheduling metadata."""

    name: str
    version: int
    enabled: bool
    trigger: str
    schedule: str
    rule_names: tuple[str, ...]
    slack_context_enabled: bool
    slack_context_limit: int
    body: str


def proactive_messages_dir() -> Path:
    """Return the directory containing bundled proactive-message policies."""
    return Path(__file__).parent


@lru_cache(maxsize=1)
def load_master_judgement() -> ProactiveMessagePolicy:
    """Load and validate ``MASTER_JUDGEMENT.md`` once per process."""
    return _load_policy(proactive_messages_dir() / _MASTER_POLICY_FILENAME)


def _load_policy(path: Path) -> ProactiveMessagePolicy:
    raw = path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(raw)
    name = _required_string(metadata, "name")
    version = metadata.get("version")
    enabled = metadata.get("enabled")
    trigger = _required_string(metadata, "trigger")
    schedule = _required_string(metadata, "schedule")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("proactive-message policy version must be a positive integer")
    if not isinstance(enabled, bool):
        raise ValueError("proactive-message policy enabled must be a boolean")
    if trigger != _SUPPORTED_TRIGGER:
        raise ValueError(f"unsupported proactive-message trigger: {trigger!r}")
    if schedule != _SUPPORTED_SCHEDULE:
        raise ValueError(f"unsupported proactive-message schedule: {schedule!r}")
    if not body:
        raise ValueError("proactive-message policy body cannot be empty")

    raw_rules = metadata.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("proactive-message policy rules must be a non-empty list")
    rule_names = tuple(rule.strip() for rule in raw_rules if isinstance(rule, str) and rule.strip())
    if len(rule_names) != len(raw_rules) or len(set(rule_names)) != len(rule_names):
        raise ValueError("proactive-message policy rules must be unique non-empty strings")

    context = metadata.get("context")
    if not isinstance(context, dict):
        raise ValueError("proactive-message policy context must be an object")
    slack_read = context.get("slack_read_messages")
    if not isinstance(slack_read, dict):
        raise ValueError("proactive-message policy must configure slack_read_messages")
    context_enabled = slack_read.get("enabled")
    context_limit = slack_read.get("limit")
    if not isinstance(context_enabled, bool):
        raise ValueError("slack_read_messages.enabled must be a boolean")
    if (
        isinstance(context_limit, bool)
        or not isinstance(context_limit, int)
        or not 1 <= context_limit <= _MAX_SLACK_CONTEXT_MESSAGES
    ):
        raise ValueError("slack_read_messages.limit must be between 1 and 100")

    return ProactiveMessagePolicy(
        name=name,
        version=version,
        enabled=enabled,
        trigger=trigger,
        schedule=schedule,
        rule_names=rule_names,
        slack_context_enabled=context_enabled,
        slack_context_limit=context_limit,
        body=body,
    )


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("proactive-message policy requires YAML frontmatter")
    end_index = normalized.find("\n---\n", 4)
    if end_index < 0:
        raise ValueError("proactive-message policy frontmatter is not closed")
    try:
        metadata = yaml.safe_load(normalized[4:end_index])
    except yaml.YAMLError as exc:
        raise ValueError("proactive-message policy frontmatter is invalid") from exc
    if not isinstance(metadata, dict):
        raise ValueError("proactive-message policy frontmatter must be an object")
    return metadata, normalized[end_index + 5 :].strip()


def _required_string(metadata: dict[str, Any], field: str) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"proactive-message policy {field} must be a non-empty string")
    return value.strip()


__all__ = ["ProactiveMessagePolicy", "load_master_judgement"]
