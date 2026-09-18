"""Background Slack Socket Mode gateway service: connection + lifecycle."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.client import BaseSocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from config.constants.gateway import DEFAULT_STOP_TIMEOUT_SECONDS
from config.constants.slack import SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS
from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.core.storage.session.binding_store import BindingStore
from gateway.transports.slack.delivery.approvals import handle_block_actions_payload
from gateway.transports.slack.delivery.feedback import record_feedback_payload
from gateway.transports.slack.processing.events import parse_events_api_payload
from gateway.transports.slack.settings import SlackGatewaySettings
from gateway.transports.slack.transport.socket_mode.heartbeat import (
    DEFAULT_HEARTBEAT_PATH,
    ConnectionHeartbeat,
)
from gateway.transports.slack.turn_stack import build_slack_turn_stack
from infrastructure.proactive_messages import ProactiveMessageService
from infrastructure.turn_host.turn_callback import TurnCallback

_EVENTS_API_REQUEST_TYPE = "events_api"
_INTERACTIVE_REQUEST_TYPE = "interactive"


class SlackGatewayBackground:
    """Control handle for the background Slack Socket Mode worker."""

    def __init__(
        self,
        *,
        socket_client: SocketModeClient,
        executor: ThreadPoolExecutor,
        bindings: BindingStore,
        heartbeat: ConnectionHeartbeat,
        proactive: ProactiveMessageService | None = None,
    ) -> None:
        self._socket_client = socket_client
        self._executor = executor
        self._bindings = bindings
        self._heartbeat = heartbeat
        self._proactive = proactive

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
        if self._proactive is not None:
            stopped = self._proactive.stop(timeout=budget.remaining) and stopped
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
    approvals = stack.approvals
    greeter = stack.greeter
    dispatcher = stack.dispatcher
    bindings = stack.bindings

    def _on_request(client: BaseSocketModeClient, request: SocketModeRequest) -> None:
        # Ack first: Slack redelivers any envelope not acked within 3 seconds.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        if request.type == _INTERACTIVE_REQUEST_TYPE:
            # Approval clicks resolve on the listener thread: turn workers may
            # all be blocked *waiting* on these buttons, so a click must never
            # need a free worker. Feedback clicks share the envelope type.
            record_feedback_payload(request.payload)
            handle_block_actions_payload(
                request.payload,
                broker=approvals,
                allowed_user_ids=settings.allowed_user_ids,
                allow_open_workspace=settings.allow_open_workspace,
            )
            return
        if request.type != _EVENTS_API_REQUEST_TYPE:
            return
        event_type = str((request.payload.get("event") or {}).get("type") or "")
        if event_type == "member_joined_channel":
            # Greeting posts a message (network call): hand it to a worker.
            executor.submit(greeter.handle, request.payload)
            return
        inbound = parse_events_api_payload(request.payload)
        if inbound is None:
            return
        executor.submit(dispatcher.dispatch, inbound)

    socket_client.socket_mode_request_listeners.append(_on_request)
    try:
        socket_client.connect()
    except Exception as exc:
        executor.shutdown(wait=False)
        stack.proactive.stop(timeout=0.0)
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
        proactive=stack.proactive,
    )
