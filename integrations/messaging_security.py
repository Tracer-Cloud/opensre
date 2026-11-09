"""Messaging security: per-user identity, allowed-users list, and DM pairing.

This module implements the identity model for inbound messaging platforms
(Telegram, Slack, Discord). It provides:

1. MessagingIdentityPolicy — per-platform allowlist and pairing config.
2. DM pairing helpers — one-time code generation, hashing, and verification.
3. Inbound message authorization — check whether a sender is allowed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import string
import time
from enum import StrEnum

from pydantic import Field, PrivateAttr

from config.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAIRING_CODE_LENGTH = 6
_PAIRING_CODE_ALPHABET = string.ascii_uppercase + string.digits

# Maximum number of failed pairing attempts before the code is invalidated.
_MAX_PAIRING_ATTEMPTS = 5

# Pairing code TTL in seconds (15 minutes).
_PAIRING_CODE_TTL_SECONDS = 900


class RejectionBehavior(StrEnum):
    """How to handle messages from non-paired users."""

    REPLY = "reply"
    DROP = "drop"


class MessagingPlatform(StrEnum):
    """Inbound-identity platforms: which gateways receive messages and run
    DM-pairing/allow-list security checks (see ``gateway/transports/telegram``,
    ``gateway/transports/slack``, ``gateway/transports/discord``, and the ``opensre messaging`` CLI).

    Distinct from ``infrastructure.scheduling.scheduler.types.Provider``, the canonical
    *delivery* vocabulary for scheduled outbound messages. Rocket.Chat is a
    delivery-only provider with no gateway inbound surface, so it is
    deliberately absent here rather than a gap to fill.
    """

    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"
    BUZZ = "buzz"


# ---------------------------------------------------------------------------
# Identity Policy Model
# ---------------------------------------------------------------------------


class MessagingIdentityPolicy(StrictConfigModel):
    """Per-platform identity policy for inbound messaging security.

    Controls which users are allowed to interact with the bot and how
    unauthenticated users are handled.

    IMPORTANT — Stable IDs only:
    All identity fields (allowed_user_ids, allowed_chat_ids) MUST use
    platform-native stable identifiers, never display names or handles:
      - Telegram: numeric ``from.id`` (survives username changes)
      - Slack: ``U02ABC123`` user ID (not @display-name)
      - Discord: snowflake user ID (not username#discriminator)
    Handles are for human-facing rendering only. Keying trust decisions
    on handles is an impersonation vector (handles can be reassigned).
    """

    allowed_user_ids: list[str] = Field(
        default_factory=list,
        description="Platform-native user IDs allowed to interact (Telegram from.id, Slack user_id, Discord member.user.id)",
    )
    allowed_chat_ids: list[str] = Field(
        default_factory=list,
        description="Optional: restrict interactions to specific channels/chats",
    )
    require_dm_pairing: bool = Field(
        default=True,
        description="Whether users must complete DM pairing before interacting",
    )
    pairing_secret_hash: str | None = Field(
        default=None,
        description="SHA-256 HMAC hash of the one-time pairing code (None = no pending pairing)",
    )
    pairing_created_at: float | None = Field(
        default=None,
        description="Unix timestamp when the pairing code was generated (for TTL enforcement)",
    )
    pairing_attempts: int = Field(
        default=0,
        description="Number of failed pairing attempts (for brute-force protection)",
    )
    rejection_behavior: RejectionBehavior = Field(
        default=RejectionBehavior.REPLY,
        description="How to handle messages from non-paired users: 'reply' or 'drop'",
    )
    inbound_enabled: bool = Field(
        default=False,
        description="Whether inbound messaging is enabled for this platform",
    )

    # Cached set view of allowed_user_ids for O(1) membership checks. Lazily
    # built on first access and rebuilt whenever this module changes
    # allowed_user_ids (see function complete_pairing). allowed_user_ids remains the
    # sole source of truth and storage format; this is a derived cache only.
    _allowed_user_id_set: frozenset[str] | None = PrivateAttr(default=None)

    def allowed_user_id_set(self) -> frozenset[str]:
        """Return a cached ``frozenset`` of allowed_user_ids for fast membership checks."""
        if self._allowed_user_id_set is None:
            self._allowed_user_id_set = frozenset(self.allowed_user_ids)
        return self._allowed_user_id_set

    def _invalidate_allowed_user_id_cache(self) -> None:
        """Drop the cached set so it is rebuilt from allowed_user_ids on next access."""
        self._allowed_user_id_set = None


# ---------------------------------------------------------------------------
# Pairing Code Helpers
# ---------------------------------------------------------------------------


def _get_hmac_key() -> bytes:
    """Derive the HMAC key from the OPENSRE_PAIRING_SECRET env var.

    **Production requirement**: Set ``OPENSRE_PAIRING_SECRET`` to a strong,
    unique secret in production deployments. Without it, the fallback key is
    derived from the machine hostname, which is not secret — if the stored
    hash leaks, an attacker could brute-force the 6-char code space offline.
    The 5-attempt online limit does not protect against offline attacks on a
    leaked hash.

    Falls back to a per-machine default derived from the hostname so that
    local development and testing work without extra configuration.
    """
    env_secret = os.environ.get("OPENSRE_PAIRING_SECRET", "")
    if env_secret:
        return env_secret.encode()
    # Fallback: derive from hostname + a fixed namespace so it's unique per machine
    # but deterministic across restarts.
    import platform

    machine_id = platform.node() or "opensre-default"
    return f"opensre-pairing-{machine_id}".encode()


def generate_pairing_code() -> str:
    """Generate a cryptographically random one-time pairing code.

    Returns a 6-character uppercase alphanumeric string.
    """
    return "".join(secrets.choice(_PAIRING_CODE_ALPHABET) for _ in range(_PAIRING_CODE_LENGTH))


def hash_pairing_code(code: str) -> str:
    """Compute a deterministic HMAC-SHA256 hash of a pairing code.

    The hash is stored in the config; the plaintext code is shown to the
    operator once and never persisted.
    """
    key = _get_hmac_key()
    return hmac.HMAC(key, code.upper().encode(), hashlib.sha256).hexdigest()


def verify_pairing_code(code: str, stored_hash: str) -> bool:
    """Verify a pairing code against its stored hash (constant-time comparison)."""
    computed = hash_pairing_code(code)
    return hmac.compare_digest(computed, stored_hash)


def _is_pairing_expired(policy: MessagingIdentityPolicy) -> bool:
    """Check if the pending pairing code has expired.

    Returns True when pairing_created_at is None (missing timestamp is
    treated as expired to be safe — legacy hashes without a timestamp
    should be regenerated).
    """
    if policy.pairing_created_at is None:
        return True
    return (time.time() - policy.pairing_created_at) > _PAIRING_CODE_TTL_SECONDS


# ---------------------------------------------------------------------------
# Authorization Check
# ---------------------------------------------------------------------------


class AuthorizationResult:
    """Result of an inbound message authorization check."""

    def __init__(
        self,
        *,
        allowed: bool,
        reason: str,
        is_pairing_attempt: bool = False,
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.is_pairing_attempt = is_pairing_attempt

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:
        return f"AuthorizationResult(allowed={self.allowed}, reason={self.reason!r})"


def authorize_inbound_message(
    *,
    policy: MessagingIdentityPolicy,
    user_id: str,
    chat_id: str | None = None,
    message_text: str | None = None,
) -> AuthorizationResult:
    """Check whether an inbound message is authorized under the given policy.

    Returns an AuthorizationResult indicating whether the message should be
    processed, and if not, why.
    """
    if not policy.inbound_enabled:
        return AuthorizationResult(
            allowed=False,
            reason="Inbound messaging is not enabled for this platform",
        )

    # This runs before the /pair check so that pairing cannot bypass chat restrictions.
    # When allowed_chat_ids is set, a None chat_id means the message is from
    # an unidentifiable context (e.g. a DM with no chat_id) — treat as blocked.
    if policy.allowed_chat_ids and (not chat_id or chat_id not in policy.allowed_chat_ids):
        return AuthorizationResult(
            allowed=False,
            reason=f"Chat {chat_id or 'N/A'} is not in the allowed chat list",
        )

    # Already-authorized users skip the pairing path entirely.
    # This prevents an allowed user from accidentally consuming a pending
    # pairing code meant for someone else.
    if user_id in policy.allowed_user_id_set():
        return AuthorizationResult(allowed=True, reason="User is authorized")

    if message_text and message_text.strip().lower().startswith("/pair "):
        if policy.pairing_secret_hash:
            return AuthorizationResult(
                allowed=True,
                reason="Pairing attempt",
                is_pairing_attempt=True,
            )
        return AuthorizationResult(
            allowed=False,
            reason="No pairing is pending",
        )

    if not policy.allowed_user_ids:
        if policy.require_dm_pairing:
            return AuthorizationResult(
                allowed=False,
                reason="No users have been paired yet. Use /pair <code> to pair.",
            )
        return AuthorizationResult(allowed=True, reason="No allowlist configured, open access")

    return AuthorizationResult(
        allowed=False,
        reason=f"User {user_id} is not in the allowed users list",
    )


def complete_pairing(
    *,
    policy: MessagingIdentityPolicy,
    user_id: str,
    code: str,
) -> tuple[bool, str]:
    """Attempt to complete DM pairing for a user.

    On success, adds the user to allowed_user_ids and clears the pairing
    secret. Returns (success, message).

    Includes brute-force protection: after MAX_PAIRING_ATTEMPTS failed
    attempts, the pairing code is invalidated. Codes also expire after
    PAIRING_CODE_TTL_SECONDS.

    IMPORTANT: The caller MUST persist the updated policy after every call,
    regardless of the return value. Failed attempts increment
    pairing_attempts; if the caller only persists on success, the counter
    resets on the next load and brute-force protection is defeated.
    """
    if not policy.pairing_secret_hash:
        return False, "No pairing is pending. Ask the operator to run `opensre messaging pair`."

    if _is_pairing_expired(policy):
        policy.pairing_secret_hash = None
        policy.pairing_created_at = None
        policy.pairing_attempts = 0
        return (
            False,
            "Pairing code has expired. Ask the operator to run `opensre messaging pair` again.",
        )

    if policy.pairing_attempts >= _MAX_PAIRING_ATTEMPTS:
        policy.pairing_secret_hash = None
        policy.pairing_created_at = None
        policy.pairing_attempts = 0
        return (
            False,
            "Too many failed attempts. Pairing code invalidated. Ask the operator to generate a new one.",
        )

    if not verify_pairing_code(code, policy.pairing_secret_hash):
        policy.pairing_attempts += 1
        remaining = _MAX_PAIRING_ATTEMPTS - policy.pairing_attempts
        if remaining <= 0:
            policy.pairing_secret_hash = None
            policy.pairing_created_at = None
            policy.pairing_attempts = 0
            return False, "Too many failed attempts. Pairing code invalidated."
        return False, f"Invalid pairing code. {remaining} attempts remaining."

    if user_id not in policy.allowed_user_id_set():
        policy.allowed_user_ids.append(user_id)
        policy._invalidate_allowed_user_id_cache()
    policy.pairing_secret_hash = None
    policy.pairing_created_at = None
    policy.pairing_attempts = 0

    logger.info("DM pairing completed for user %s", user_id)
    return True, "Pairing successful! You can now interact with the bot."


# ---------------------------------------------------------------------------
# Audit Logging
# ---------------------------------------------------------------------------


def message_hash(text: str) -> str:
    """Short content hash for audit entries — bodies are never logged in plaintext."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def audit_log_inbound_message(
    *,
    platform: str,
    user_id: str,
    chat_id: str | None,
    message_hash: str | None = None,
    authorized: bool,
    reason: str,
) -> None:
    """Emit a structured audit log entry for an inbound message.

    Message body is hashed (not stored in plaintext) to enable misuse
    investigation without leaking content.
    """
    logger.info(
        "[messaging-audit] platform=%s user_id=%s chat_id=%s authorized=%s reason=%s msg_hash=%s",
        platform,
        user_id,
        chat_id or "N/A",
        authorized,
        reason,
        message_hash or "N/A",
    )
