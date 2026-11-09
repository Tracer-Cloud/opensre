"""Inbound authorization helpers for the Discord gateway."""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.gateway import ROTATE_SESSION
from gateway.core.middleware.identity_policy import (
    load_identity_policy,
)
from integrations.messaging_security import (
    AuthorizationResult,
    MessagingIdentityPolicy,
    MessagingPlatform,
    audit_log_inbound_message,
    authorize_inbound_message,
    complete_pairing,
    message_hash,
)

_PLATFORM = MessagingPlatform.DISCORD.value
_HELP_TEXT = (
    "OpenSRE Discord gateway.\n"
    "DM the bot or @mention it in a server to chat with the agent.\n"
    "Commands: /new (new session), /help, /pair <code>"
)
_EMPTY_ALLOWLIST_REASON = (
    "Discord allowlist is empty; run `opensre messaging allow -p discord -u <id>`, "
    "set DISCORD_ALLOWED_USERS, or set DISCORD_ALLOW_OPEN_GUILD=1"
)


@dataclass(frozen=True)
class DiscordInboundDecision:
    """Authorization outcome for one inbound Discord message."""

    allowed: bool
    reply_text: str = ""
    persist_policy: bool = False
    updated_policy: MessagingIdentityPolicy | None = None


def _audit(
    *,
    user_id: str,
    channel_id: str,
    text: str,
    authorized: bool,
    reason: str,
) -> None:
    audit_log_inbound_message(
        platform=_PLATFORM,
        user_id=user_id,
        chat_id=channel_id,
        message_hash=message_hash(text),
        authorized=authorized,
        reason=reason,
    )


def _merge_env_allowlist(
    policy: MessagingIdentityPolicy,
    env_allowed_user_ids: list[str],
) -> MessagingIdentityPolicy:
    if env_allowed_user_ids and not policy.allowed_user_ids:
        policy.allowed_user_ids = list(env_allowed_user_ids)
        policy.inbound_enabled = True
        policy._invalidate_allowed_user_id_cache()
    return policy


def enforce_inbound_discord_message_security(
    *,
    user_id: str,
    channel_id: str,
    text: str,
    env_allowed_user_ids: list[str],
    allow_open_guild: bool = False,
    is_guild_message: bool = False,
) -> DiscordInboundDecision:
    """Authorize inbound Discord text and handle /pair, /new, /help.

    ``allow_open_guild`` only ever applies to messages that arrived through a
    guild (``is_guild_message=True``). DMs always require the allowlist:
    anyone sharing a server with the bot can DM it, so an open-DM mode would
    let arbitrary users consume credits and reach agent turns.
    """
    _record, policy = load_identity_policy(_PLATFORM)
    policy = _merge_env_allowlist(policy, env_allowed_user_ids)
    open_guild_applies = allow_open_guild and is_guild_message

    stripped = text.strip()
    lower = stripped.lower()

    if lower.startswith("/pair "):
        code = stripped.split(maxsplit=1)[1] if " " in stripped else ""
        ok, msg = complete_pairing(policy=policy, user_id=user_id, code=code)
        _audit(user_id=user_id, channel_id=channel_id, text=text, authorized=ok, reason=msg)
        return DiscordInboundDecision(
            allowed=False,
            reply_text=msg,
            persist_policy=True,
            updated_policy=policy,
        )

    if lower in {"/help", "/start"}:
        _audit(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            authorized=True,
            reason="builtin command",
        )
        return DiscordInboundDecision(allowed=False, reply_text=_HELP_TEXT)

    if not policy.allowed_user_ids and not open_guild_applies:
        reason = (
            "open guild mode does not cover DMs; allowlist required"
            if allow_open_guild
            else _EMPTY_ALLOWLIST_REASON
        )
        _audit(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            authorized=False,
            reason=reason,
        )
        return DiscordInboundDecision(allowed=False)

    if open_guild_applies and not policy.allowed_user_ids:
        result = AuthorizationResult(allowed=True, reason="open guild")
    else:
        result = authorize_inbound_message(
            policy=policy,
            user_id=user_id,
            chat_id=channel_id,
            message_text=text,
        )

    if lower == "/new":
        if not result:
            _audit(
                user_id=user_id,
                channel_id=channel_id,
                text=text,
                authorized=False,
                reason=result.reason,
            )
            return DiscordInboundDecision(allowed=False)
        _audit(
            user_id=user_id,
            channel_id=channel_id,
            text=text,
            authorized=True,
            reason="session rotate",
        )
        return DiscordInboundDecision(allowed=True, reply_text=ROTATE_SESSION)

    _audit(
        user_id=user_id,
        channel_id=channel_id,
        text=text,
        authorized=bool(result),
        reason=result.reason,
    )
    return DiscordInboundDecision(allowed=bool(result))


__all__ = [
    "DiscordInboundDecision",
    "enforce_inbound_discord_message_security",
]
