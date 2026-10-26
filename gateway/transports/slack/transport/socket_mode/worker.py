"""Background Slack Socket Mode gateway service: connection + lifecycle."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from slack_sdk.socket_mode import SocketModeClient

from config.constants.gateway import DEFAULT_STOP_TIMEOUT_SECONDS
from config.constants.slack import SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS
from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.core.storage.session.binding_store import BindingStore
from gateway.transports.slack.settings import SlackGatewaySettings
from gateway.transports.slack.transport.socket_mode.dedupe import (
    BoundedHandledSlackEventRepository,
)
from gateway.transports.slack.transport.socket_mode.heartbeat import (
    DEFAULT_HEARTBEAT_PATH,
    ConnectionHeartbeat,
)
from gateway.transports.slack.transport.socket_mode.listener import build_socket_mode_listener
from gateway.transports.slack.turn_stack import build_slack_turn_stack
from infrastructure.turn_host.turn_callback import TurnCallback


class SlackGatewayBackground:
    """Control handle for the background Slack Socket Mode worker."""

    def __init__(
        self,
        *,
        socket_client: SocketModeClient,
        executor: ThreadPoolExecutor,
        bindings: BindingStore,
        heartbeat: ConnectionHeartbeat,
    ) -> None:
        self._socket_client = socket_client
        self._executor = executor
        self._bindings = bindings
        self._heartbeat = heartbeat

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        """Disconnect from Slack, wait up to ``timeout`` for in-flight turns, and clean up."""
        budget = ShutdownBudget(timeout)
        started = budget.mark()
        self._heartbeat.stop(timeout=budget.take(SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS))
        budget.consume(started)
        try:
            self._socket_client.close()
        except Exception:
            logging.getLogger(__name__).debug("[slack-gateway] close failed", exc_info=True)
        # shutdown() has no timeout parameter, so bound the wait with a joiner thread.
        waiter = threading.Thread(
            target=lambda: self._executor.shutdown(wait=True, cancel_futures=False),
            name="SlackGatewayShutdown",
            daemon=True,
        )
        waiter.start()
        waiter.join(budget.remaining)
        stopped = not waiter.is_alive()
        try:
            self._bindings.close()
        except Exception:
            logging.getLogger(__name__).debug(
                "[slack-gateway] binding store close failed", exc_info=True
            )
        return stopped


def start_slack_gateway_background(
    *,
    settings: SlackGatewaySettings,
    logger: logging.Logger,
    handler: TurnCallback,
) -> SlackGatewayBackground:
    """Connect to Slack over Socket Mode and dispatch inbound messages until stopped."""
    stack = build_slack_turn_stack(settings=settings, logger=logger, handler=handler)
    socket_client = SocketModeClient(app_token=settings.app_token, web_client=stack.web_client)
    executor = stack.executor
    bindings = stack.bindings
    # Process-local is enough here: Socket Mode has one consumer per app token,
    # so every redelivery of an event reaches this process.
    listener = build_socket_mode_listener(
        settings=settings,
        stack=stack,
        handled_events=BoundedHandledSlackEventRepository(),
        logger=logger,
    )
    socket_client.socket_mode_request_listeners.append(listener)
    try:
        socket_client.connect()
    except Exception as exc:
        executor.shutdown(wait=False)
        bindings.close()
        raise GatewayConfigurationError(f"Slack Socket Mode connect failed: {exc}") from exc

    logger.info("[slack-gateway] socket mode connected")
    heartbeat = ConnectionHeartbeat(
        path=settings.heartbeat_path or DEFAULT_HEARTBEAT_PATH,
        is_alive=socket_client.is_connected,
    )
    heartbeat.start()
    return SlackGatewayBackground(
        socket_client=socket_client,
        executor=executor,
        bindings=bindings,
        heartbeat=heartbeat,
    )
