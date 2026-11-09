"""Shared Click options and identity validation for messaging command surfaces."""

from __future__ import annotations

import click

from integrations.messaging_security import MessagingPlatform


def validate_allow_user_id(platform: str, user_id: str) -> str:
    """Normalize + validate a user id for ``allow``; raise on non-native values.

    Inbound authorization matches the platform-native user id (Telegram
    ``from.id``, Slack ``user_id``, Discord member id) — never a ``@username``.
    Accepting a handle silently produces an allow-list entry that can never
    match a real sender, which is exactly the "user X is not allowed" trap.
    """
    uid = user_id.strip()
    if not uid:
        raise click.BadParameter("user id must not be empty.", param_hint="--user-id")
    if uid.startswith("@"):
        raise click.BadParameter(
            f"'{uid}' is a @username, not a platform user id. Inbound auth matches the "
            "numeric platform user id, not the handle — get it from @userinfobot or the "
            "bot's getUpdates response.",
            param_hint="--user-id",
        )
    if platform == MessagingPlatform.TELEGRAM.value and not uid.isdigit():
        raise click.BadParameter(
            f"Telegram user ids are numeric (e.g. 6514715683); got '{uid}'. Use the numeric "
            "from.id, not a username or display name.",
            param_hint="--user-id",
        )
    if platform == MessagingPlatform.BUZZ.value and not _is_hex_pubkey(uid):
        raise click.BadParameter(
            f"Buzz user ids are 64-char hex Nostr pubkeys; got '{uid}'. Use the sender's "
            "hex pubkey (from `buzz messages get` output), not an npub or display name.",
            param_hint="--user-id",
        )
    return uid


def _is_hex_pubkey(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def messaging_parameters(action: str) -> list[click.Parameter]:
    """Build fresh option definitions for one messaging action."""
    parameters: list[click.Parameter] = [
        click.Option(
            ["--platform", "-p"],
            type=click.Choice(
                [platform.value for platform in MessagingPlatform], case_sensitive=False
            ),
            required=True,
            help="Messaging platform to pair with." if action == "pair" else "Messaging platform.",
        )
    ]
    if action in {"allow", "revoke"}:
        direction = "add to" if action == "allow" else "remove from"
        parameters.append(
            click.Option(
                ["--user-id", "-u"],
                required=True,
                help=f"Platform-native user ID to {direction} the allowed list.",
            )
        )
    return parameters
