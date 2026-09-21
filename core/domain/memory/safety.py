"""Safety checks for durable memory ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass

_REDACTION_PLACEHOLDER = "[REDACTED]"

_BENIGN_SECRET_VALUES = frozenset(
    {
        "configured",
        "disabled",
        "enabled",
        "expired",
        "hidden",
        "invalid",
        "missing",
        "redacted",
        "required",
        "revoked",
        "rotated",
        "set",
        "unset",
    }
)

_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_PROVIDER_TOKEN_RE = re.compile(
    r"\b(?:"
    r"AKIA[0-9A-Z]{16}|"
    r"ASIA[0-9A-Z]{16}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"xox[abprs]-[A-Za-z0-9-]{10,}|"
    r"sk-[A-Za-z0-9_-]{16,}|"
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r")\b"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_URL_CREDENTIAL_RE = re.compile(r"://[^/\s:@]+:[^/\s:@]+@")
_LABELED_SECRET_RE = re.compile(
    r"(?i)\b("
    r"(?:[a-z0-9]+[_ -]+)*(?:"
    r"api[_ -]?key|api[_ -]?token|access[_ -]?token|auth[_ -]?token|"
    r"bearer[_ -]?token|refresh[_ -]?token|session[_ -]?token|"
    r"client[_ -]?secret|secret(?:[_ -]?key)?|password|passwd|"
    r"private[_ -]?key|credential(?:s)?"
    r")"
    r")\b\s*(?:is|=|:)\s*['\"]?([^\s'\"`]{4,})"
)


@dataclass(frozen=True)
class MemorySafetyIssue:
    """A non-secret description of why memory ingestion was rejected."""

    rule: str


def find_memory_safety_issues(*texts: str) -> tuple[MemorySafetyIssue, ...]:
    """Return safety issues for text that should not be persisted as memory.

    This is intentionally conservative and value-free: callers can report rule
    names without echoing the secret-like text back to the user or logs.
    """
    text = "\n".join(part for part in texts if part)
    if not text:
        return ()

    rules: list[str] = []
    if _PRIVATE_KEY_RE.search(text):
        rules.append("private_key_block")
    if _PROVIDER_TOKEN_RE.search(text):
        rules.append("provider_token")
    if _JWT_RE.search(text):
        rules.append("jwt_token")
    if _URL_CREDENTIAL_RE.search(text):
        rules.append("url_credentials")
    if _contains_labeled_secret(text):
        rules.append("labeled_secret")
    return tuple(MemorySafetyIssue(rule=rule) for rule in rules)


def redact_memory_unsafe_text(text: str) -> str:
    """Replace secret-like spans with a placeholder before sending text to an LLM.

    Used by session-end extraction so credential-shaped content in a transcript
    is not forwarded to the classification provider even when it is also blocked
    from durable storage.
    """
    if not text:
        return text

    redacted = _PRIVATE_KEY_RE.sub(_REDACTION_PLACEHOLDER, text)
    redacted = _PROVIDER_TOKEN_RE.sub(_REDACTION_PLACEHOLDER, redacted)
    redacted = _JWT_RE.sub(_REDACTION_PLACEHOLDER, redacted)
    redacted = _URL_CREDENTIAL_RE.sub(f"://{_REDACTION_PLACEHOLDER}@", redacted)

    def _replace_labeled(match: re.Match[str]) -> str:
        label = match.group(1)
        value = match.group(2).strip(".,;)")
        if not _looks_like_secret_value(value):
            return match.group(0)
        # Preserve the surrounding delimiter shape loosely: label + separator.
        # Locate the value via its capture-group offset; ``str.find`` could match
        # the value as a substring of the label and collapse the separator.
        value_offset = match.start(2) - match.start(0)
        separator = match.group(0)[len(label) : value_offset]
        return f"{label}{separator}{_REDACTION_PLACEHOLDER}"

    return _LABELED_SECRET_RE.sub(_replace_labeled, redacted)


def _contains_labeled_secret(text: str) -> bool:
    for match in _LABELED_SECRET_RE.finditer(text):
        value = match.group(2).strip(".,;)")
        if _looks_like_secret_value(value):
            return True
    return False


def _looks_like_secret_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _BENIGN_SECRET_VALUES:
        return False
    if len(value) >= 20:
        return True
    has_alpha = any(ch.isalpha() for ch in value)
    has_digit = any(ch.isdigit() for ch in value)
    has_symbol = any(ch in "-_/+=.:" for ch in value)
    return len(value) >= 6 and has_alpha and (has_digit or has_symbol)


__all__ = [
    "MemorySafetyIssue",
    "find_memory_safety_issues",
    "redact_memory_unsafe_text",
]
