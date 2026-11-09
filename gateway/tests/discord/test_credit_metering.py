"""Discord binds its turn charge for post-capacity admission."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.gateway import CREDITS_DENIED_MESSAGE
from core.agent_harness.session import InMemorySessionStore, SessionCore
from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.tests.billing.turn_metering_harness import metered_callback
from gateway.transports.discord.dispatcher import DiscordTurnDispatcher
from gateway.transports.discord.events import DiscordInboundMessage
from gateway.transports.discord.security import DiscordInboundDecision
from gateway.transports.discord.settings import DiscordGatewaySettings

_ORG = "org_discord_credits"


class _SessionResolver:
    def __init__(self) -> None:
        self._session = SessionCore(store=InMemorySessionStore())

    def resolve(self, **_kwargs: object) -> SessionCore:
        return self._session

    def rotate(self, **_kwargs: object) -> SessionCore:
        return self._session


def test_denied_credits_bill_the_org_and_never_reach_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, _ORG)
    charges: list[tuple[str, str]] = []

    def _deny(organization_id: str, *, reason: str, **_kwargs: object) -> CreditsOutcome:
        charges.append((organization_id, reason))
        return CreditsOutcome.DENIED

    monkeypatch.setattr(turn_metering, "consume_credits", _deny)
    agent = MagicMock()
    dispatcher = DiscordTurnDispatcher(
        settings=DiscordGatewaySettings(
            bot_token="discord-test-token",
            allowed_user_ids=["U1"],
            status_update_interval_seconds=0.01,
        ),
        bot_token="discord-test-token",
        session_resolver=_SessionResolver(),  # type: ignore[arg-type]
        handler=metered_callback(agent),  # type: ignore[arg-type]
        logger=logging.getLogger("test.discord.credits"),
    )
    inbound = DiscordInboundMessage(
        guild_id="G1",
        user_id="U1",
        channel_id="C1",
        message_id="M1",
        thread_id="T1",
        text="hello",
    )

    with (
        patch(
            "gateway.transports.discord.dispatcher.enforce_inbound_discord_message_security",
            return_value=DiscordInboundDecision(allowed=True),
        ),
        patch("gateway.core.middleware.inbound_decision.persist_policy_if_needed"),
        patch("gateway.transports.discord.dispatcher.add_reaction") as add_reaction,
        patch("gateway.transports.discord.dispatcher.remove_reaction") as remove_reaction,
        patch("gateway.transports.discord.turn_output.send_message", return_value="status-1"),
        patch("gateway.transports.discord.turn_output.edit_message_with_components") as edit,
    ):
        dispatcher.dispatch(inbound)

    assert charges == [(_ORG, "discord_turn")]
    agent.assert_not_called()
    assert edit.call_args.kwargs["content"] == CREDITS_DENIED_MESSAGE
    remove_reaction.assert_called_once_with(
        channel_id="C1",
        message_id="M1",
        emoji="👀",
        bot_token="discord-test-token",
    )
    assert any(call.kwargs["emoji"] == "❌" for call in add_reaction.call_args_list)
    assert not any(call.kwargs["emoji"] == "✅" for call in add_reaction.call_args_list)
