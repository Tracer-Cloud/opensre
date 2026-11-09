from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from gateway.transports.discord.security import enforce_inbound_discord_message_security

_SECURITY = "gateway.transports.discord.security"


@pytest.fixture(autouse=True)
def mock_integration_store() -> Iterator[None]:
    with (
        patch("gateway.core.middleware.identity_policy.get_integration", return_value=None),
        patch("gateway.core.middleware.identity_policy.upsert_instance"),
        patch(f"{_SECURITY}.audit_log_inbound_message"),
    ):
        yield


def test_allowlisted_user_is_authorized_in_dm() -> None:
    decision = enforce_inbound_discord_message_security(
        user_id="111",
        channel_id="222",
        text="check disk usage",
        env_allowed_user_ids=["111"],
        is_guild_message=False,
    )
    assert decision.allowed is True


def test_unlisted_user_is_denied() -> None:
    decision = enforce_inbound_discord_message_security(
        user_id="666",
        channel_id="222",
        text="check disk usage",
        env_allowed_user_ids=["111"],
        is_guild_message=True,
    )
    assert decision.allowed is False
    assert decision.reply_text == ""


def test_empty_allowlist_denied_without_open_guild() -> None:
    decision = enforce_inbound_discord_message_security(
        user_id="anybody",
        channel_id="222",
        text="hello",
        env_allowed_user_ids=[],
        allow_open_guild=False,
        is_guild_message=True,
    )
    assert decision.allowed is False


def test_open_guild_authorizes_guild_message_with_empty_allowlist() -> None:
    decision = enforce_inbound_discord_message_security(
        user_id="anybody",
        channel_id="222",
        text="hello",
        env_allowed_user_ids=[],
        allow_open_guild=True,
        is_guild_message=True,
    )
    assert decision.allowed is True


def test_open_guild_never_authorizes_dms() -> None:
    """Open-guild trust must not extend to DMs: anyone sharing a server with
    the bot can DM it, so DMs always require the explicit allowlist."""
    decision = enforce_inbound_discord_message_security(
        user_id="anybody",
        channel_id="222",
        text="hello",
        env_allowed_user_ids=[],
        allow_open_guild=True,
        is_guild_message=False,
    )
    assert decision.allowed is False


def test_open_guild_dm_still_allows_allowlisted_user() -> None:
    decision = enforce_inbound_discord_message_security(
        user_id="111",
        channel_id="222",
        text="hello",
        env_allowed_user_ids=["111"],
        allow_open_guild=True,
        is_guild_message=False,
    )
    assert decision.allowed is True
