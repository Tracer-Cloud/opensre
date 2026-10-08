"""Socket Mode envelope routing: ack, then hand each envelope's work to its owner.

``events_api`` envelopes are claimed by ``event_id`` before their work is
queued, so a redelivery (``retry_attempt`` set, or a second copy in flight on
another listener thread) runs nothing. Interactive envelopes carry no
``event_id`` and are not de-duplicated: resolving an approval twice is a no-op
at the broker, while dropping a click strands a turn waiting on it. Slash
commands are not handled.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial

from slack_sdk.socket_mode.client import BaseSocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from gateway.core.storage.events.repository import HandledSlackEventRepository
from gateway.transports.slack.delivery.approvals import handle_block_actions_payload
from gateway.transports.slack.delivery.feedback import record_feedback_payload
from gateway.transports.slack.processing.events import parse_events_api_payload
from gateway.transports.slack.settings import SlackGatewaySettings
from gateway.transports.slack.turn_stack import SlackTurnStack

_EVENTS_API_REQUEST_TYPE = "events_api"
_INTERACTIVE_REQUEST_TYPE = "interactive"
_MEMBER_JOINED_CHANNEL_EVENT = "member_joined_channel"

#: A ``SocketModeClient.socket_mode_request_listeners`` entry.
SocketModeListener = Callable[[BaseSocketModeClient, SocketModeRequest], None]


def build_socket_mode_listener(
    *,
    settings: SlackGatewaySettings,
    stack: SlackTurnStack,
    handled_events: HandledSlackEventRepository,
    logger: logging.Logger,
) -> SocketModeListener:
    """Return the request listener for one Socket Mode connection.

    Slack calls it from several threads at once, so ``handled_events`` must be
    thread-safe. A claim is released when its work cannot be queued, so a
    later redelivery of that event still runs.
    """

    def _queue_once(event_id: str, work: Callable[[], object]) -> None:
        if not event_id:
            stack.executor.submit(work)  # nothing stable to de-duplicate on
            return
        if not handled_events.claim(event_id):
            logger.info("[slack-gateway] dropped duplicate socket mode event event_id=%s", event_id)
            return
        try:
            stack.executor.submit(work)
        except Exception:
            released = handled_events.release(event_id)
            logger.warning(
                "[slack-gateway] socket mode event %s was not queued (claim released=%s)",
                event_id,
                released,
                exc_info=True,
            )
            return
        if not handled_events.confirm(event_id):
            logger.error(
                "[slack-gateway] queued socket mode event %s but could not confirm the claim",
                event_id,
            )

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
                broker=stack.approvals,
                allowed_user_ids=settings.allowed_user_ids,
                allow_open_workspace=settings.allow_open_workspace,
            )
            return
        if request.type != _EVENTS_API_REQUEST_TYPE:
            return
        payload = request.payload
        event_id = str(payload.get("event_id") or "")
        event_type = str((payload.get("event") or {}).get("type") or "")
        if event_type == _MEMBER_JOINED_CHANNEL_EVENT:
            # Greeting posts a message (network call): hand it to a worker.
            _queue_once(event_id, partial(stack.greeter.handle, payload))
            return
        inbound = parse_events_api_payload(payload)
        if inbound is None:
            return
        _queue_once(event_id, partial(stack.dispatcher.dispatch, inbound))

    return _on_request


__all__ = ["SocketModeListener", "build_socket_mode_listener"]
