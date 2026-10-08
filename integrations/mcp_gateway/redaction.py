"""Remove the configured credential from server-controlled payloads."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from infrastructure.safety.secret_redaction import redact_text

_INCOMPLETE_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*$"
)


def redacted_text_preview(text: str, auth_token: str, *, max_chars: int) -> str:
    """Redact a bounded window, removing incomplete credentials at its cut boundary."""
    if len(text) <= max_chars:
        return redact_text(scrub_configured_token(text, auth_token))
    if len(auth_token) > max_chars:
        if auth_token in text:
            return "[oversized value omitted]"
        window = text[:max_chars]
    else:
        window = text[: max_chars + len(auth_token)]
    safe = redact_text(scrub_configured_token(window, auth_token))
    safe = _INCOMPLETE_PRIVATE_KEY.sub("[REDACTED:private_key]", safe)
    if len(safe) > max_chars:
        safe = re.sub(r"\S+$", "", safe[:max_chars]).rstrip()
    return safe + " [truncated]"


def scrub_configured_token(value: Any, auth_token: str) -> Any:
    """Copy JSON payloads with exact configured-token occurrences replaced."""
    if not auth_token:
        return value
    if isinstance(value, str):
        return value.replace(auth_token, "[redacted]")
    if isinstance(value, dict):
        copied = {}
        for key, item in value.items():
            safe_key = scrub_configured_token(key, auth_token)
            unique_key = safe_key
            suffix = 1
            while unique_key in copied:
                suffix += 1
                unique_key = f"{safe_key} #{suffix}"
            copied[unique_key] = scrub_configured_token(item, auth_token)
        return copied
    if isinstance(value, list):
        return [scrub_configured_token(item, auth_token) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_configured_token(item, auth_token) for item in value)
    return value


def public_tool_name(name: str, auth_token: str) -> str:
    """Return a stable callable alias when an advertised name contains the credential."""
    if (not auth_token or auth_token not in name) and redact_text(name) == name:
        return name
    alias = "mcp_tool_" + hashlib.sha256(name.encode()).hexdigest()
    replacement = "-" if not auth_token or auth_token[0] != "-" else "_"
    while auth_token and auth_token in alias:
        alias = alias.replace(auth_token, replacement)
    return alias
