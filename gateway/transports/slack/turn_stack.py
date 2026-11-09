"""Everything a Slack inbound transport needs to run turns, built once.

Socket Mode and Events API HTTP differ only in how a payload arrives. Both
then need the same collaborators — Web API client, dispatcher, approval broker,
channel greeter, binding store, and the turn executor — so the assembly lives
here and each transport owns only its own delivery mechanism.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from slack_sdk.web import WebClient

from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.storage import SessionResolver
from gateway.core.storage.session.binding_store import BindingStore, open_binding_store
from gateway.transports.slack.client import SlackWebApiClient
from gateway.transports.slack.delivery.channel_intro import ChannelIntroGreeter
from gateway.transports.slack.processing.dispatcher import SlackTurnDispatcher
from gateway.transports.slack.settings import SlackGatewaySettings
from infrastructure.turn_host.turn_callback import TurnCallback

_PLATFORM_SLACK = "slack"


def resolve_bot_user_id(web_client: WebClient, logger: logging.Logger) -> str:
    """Return the bot's own Slack user id via auth.test, or '' on failure.

    Thread seeding labels the bot's own replies by author with this, rather
    than by matching message text.
    """
    try:
        return str(web_client.auth_test().get("user_id") or "")
    except Exception:
        logger.debug("[slack-gateway] auth.test for bot_user_id failed", exc_info=True)
        return ""


@dataclass(frozen=True)
class SlackTurnStack:
    """The per-process Slack collaborators shared by both inbound transports."""

    web_client: WebClient
    executor: ThreadPoolExecutor
    dispatcher: SlackTurnDispatcher
    greeter: ChannelIntroGreeter
    approvals: ApprovalBroker
    bindings: BindingStore
    bot_user_id: str

    def close(self) -> None:
        """Release the binding store; the owning transport stops the executor."""
        try:
            self.bindings.close()
        except Exception:
            logging.getLogger(__name__).debug(
                "[slack-gateway] binding store close failed", exc_info=True
            )


def build_slack_turn_stack(
    *,
    settings: SlackGatewaySettings,
    logger: logging.Logger,
    handler: TurnCallback,
) -> SlackTurnStack:
    """Construct the Slack turn collaborators for one gateway process."""
    web_client = WebClient(token=settings.bot_token)
    executor = ThreadPoolExecutor(
        max_workers=settings.max_concurrent_turns,
        thread_name_prefix="SlackGatewayTurn",
    )
    bot_user_id = resolve_bot_user_id(web_client, logger)
    approvals = ApprovalBroker()
    messaging = SlackWebApiClient(web_client)
    bindings = open_binding_store()
    dispatcher = SlackTurnDispatcher(
        settings=settings,
        messaging=messaging,
        session_resolver=SessionResolver(bindings, platform=_PLATFORM_SLACK),
        handler=handler,
        logger=logger,
        bot_user_id=bot_user_id,
        approvals=approvals,
    )
    return SlackTurnStack(
        web_client=web_client,
        executor=executor,
        dispatcher=dispatcher,
        greeter=ChannelIntroGreeter(messaging=messaging, bot_user_id=bot_user_id),
        approvals=approvals,
        bindings=bindings,
        bot_user_id=bot_user_id,
    )


__all__ = ["SlackTurnStack", "build_slack_turn_stack", "resolve_bot_user_id"]
