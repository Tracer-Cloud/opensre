"""Slack's own HTTP listener for Events API inbound.

A dedicated app on its own port, not a route added to :mod:`gateway.web`: that
app is the health and alert surface, and the package DAG forbids it importing a
chat transport. Keeping the listener here also keeps the two inbound transports
symmetrical — Socket Mode owns its connection in :mod:`.worker`, Events API
owns its server here.

Slack retries any delivery it does not see answered within three seconds, so
both routes verify, hand the message to ``handler`` on a worker thread, and
return immediately. The turn never runs inside the request.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from config.constants.gateway import DEFAULT_STOP_TIMEOUT_SECONDS
from gateway.core.lifecycle.errors import GatewayTransportFailedError
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.core.storage.events.repository import HandledSlackEventRepository
from gateway.transports.slack.delivery.approvals import handle_block_actions_payload
from gateway.transports.slack.delivery.feedback import record_feedback_payload
from gateway.transports.slack.processing.events import SlackInboundMessage
from gateway.transports.slack.settings import SlackGatewaySettings
from gateway.transports.slack.transport.events_api.receiver import (
    SlackHttpStatus,
    admit_slack_http_request,
    admit_slack_interactivity_request,
)
from infrastructure.request_body_limit import RequestBodyLimitMiddleware

#: Starts a turn for one inbound message without blocking the request.
SubmitTurn = Callable[[SlackInboundMessage], None]


@dataclass
class ListenerGate:
    """Whether this listener may still act on deliveries.

    Closed when the transport failed to start or has stopped. Checked by
    every route: a uvicorn thread can outlive either event, and an
    interactivity click is handled inline, so nothing else would stop a
    residual listener resolving approvals for a dead transport.
    """

    accepting: bool = True


EVENTS_PATH = "/slack/events"
# Slack posts button clicks to a separate request URL. Without this route
# approvals and feedback silently stop working while turns still run.
INTERACTIVITY_PATH = "/slack/interactivity"

# How long a failed start waits for the listener thread to unwind before
# giving up on it; the thread is a daemon, so it cannot block exit.
_FAILED_START_JOIN_SECONDS = 5.0

logger = logging.getLogger("gateway")


@dataclass
class SlackHttpServerHandle:
    """A running Slack HTTP listener."""

    server: uvicorn.Server
    thread: threading.Thread
    workers: ThreadPoolExecutor
    gate: ListenerGate
    bound_host: str
    bound_port: int
    background_stop: Callable[[float], bool] | None = None

    @property
    def bound_address(self) -> str:
        return f"{self.bound_host}:{self.bound_port}"

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        """Stop serving and report whether the listener thread ended in time.

        Signature matches ``gateway.startup.TransportWorker`` so the
        composition root can hold either inbound transport in one handle.
        """
        self.gate.accepting = False  # refuse first; the thread may outlive the join
        self.server.should_exit = True
        budget = ShutdownBudget(timeout)
        self.thread.join(timeout=budget.remaining)
        self.workers.shutdown(wait=False, cancel_futures=True)
        background_stopped = (
            self.background_stop(budget.remaining) if self.background_stop is not None else True
        )
        return not self.thread.is_alive() and background_stopped


def build_slack_http_app(
    *,
    settings: SlackGatewaySettings,
    approvals: ApprovalBroker,
    handled_events: HandledSlackEventRepository,
    submit_turn: SubmitTurn,
    gate: ListenerGate,
) -> FastAPI:
    """Return the Slack listener app.

    ``submit_turn`` takes one :class:`SlackInboundMessage` and starts the turn
    without blocking — the route must answer Slack first.
    """
    app = FastAPI()
    # Slack's signature covers the raw body, so both routes must read it before
    # they can authenticate the caller. Bound the read itself.
    app.add_middleware(RequestBodyLimitMiddleware)

    async def _admit(request: Request) -> Response:
        if not gate.accepting:
            return JSONResponse(
                {"error": "unavailable"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE
            )
        body = await request.body()  # raw bytes: the signature covers them exactly
        outcome = admit_slack_http_request(
            headers=dict(request.headers),
            body=body,
            signing_secret=settings.signing_secret,
            handled_events=handled_events,
        )
        if outcome.status is SlackHttpStatus.REJECTED:
            # Detail stays server-side (CWE-209): the caller is unauthenticated.
            logger.warning("slack http rejected: %s", outcome.reason)
            return JSONResponse({"error": "unauthorized"}, status_code=HTTPStatus.UNAUTHORIZED)
        if outcome.status is SlackHttpStatus.CHALLENGE:
            return PlainTextResponse(outcome.challenge)
        if outcome.status is SlackHttpStatus.ACCEPTED and outcome.message is not None:
            try:
                submit_turn(outcome.message)
            except RuntimeError:
                # Executor gone (failed start / stop). Release the provisional
                # claim, then 503 so Slack retries. If release fails, still 500
                # to page — but the next ``claim`` may reclaim the uncommitted
                # row, so the event is not permanently answered as a duplicate.
                released = handled_events.release(outcome.event_id)
                logger.warning("slack http accepted an event with no turn executor")
                if not released:
                    logger.error(
                        "slack http could not release event %s; retry may reclaim "
                        "the provisional claim",
                        outcome.event_id,
                    )
                    return JSONResponse(
                        {"error": "internal error"},
                        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                return JSONResponse(
                    {"error": "unavailable"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE
                )
            if not handled_events.confirm(outcome.event_id):
                # Turn is already queued; prefer 200 over 5xx (which would
                # invite a reclaim double-run).
                logger.error(
                    "slack http queued event %s but could not confirm the claim",
                    outcome.event_id,
                )
        return JSONResponse({"ok": True})

    async def _interactivity(request: Request) -> Response:
        if not gate.accepting:
            return JSONResponse(
                {"error": "unavailable"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE
            )
        body = await request.body()
        outcome = admit_slack_interactivity_request(
            headers=dict(request.headers),
            body=body,
            signing_secret=settings.signing_secret,
        )
        if outcome.status is SlackHttpStatus.REJECTED:
            logger.warning("slack interactivity rejected: %s", outcome.reason)
            return JSONResponse({"error": "unauthorized"}, status_code=HTTPStatus.UNAUTHORIZED)
        if outcome.payload is not None:
            # Resolved on the request thread, never the turn executor: workers
            # may all be blocked *waiting* on these very buttons, so a click
            # must never need a free worker to be recorded.
            record_feedback_payload(outcome.payload)
            handle_block_actions_payload(
                outcome.payload,
                broker=approvals,
                allowed_user_ids=settings.allowed_user_ids,
                allow_open_workspace=settings.allow_open_workspace,
            )
        return JSONResponse({"ok": True})

    app.add_api_route(EVENTS_PATH, _admit, methods=["POST"])
    app.add_api_route(INTERACTIVITY_PATH, _interactivity, methods=["POST"])
    return app


def serve_slack_http_in_thread(
    *,
    app: FastAPI,
    host: str = "0.0.0.0",
    port: int,
    workers: ThreadPoolExecutor,
    gate: ListenerGate,
    startup_timeout: float = 10.0,
    background_stop: Callable[[float], bool] | None = None,
) -> SlackHttpServerHandle:
    """Start uvicorn on ``app`` and wait until it is bound."""
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None))
    thread = threading.Thread(target=server.run, name="slack-http", daemon=True)
    thread.start()

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if server.started and server.servers:
            bound = server.servers[0].sockets[0].getsockname()
            return SlackHttpServerHandle(
                server,
                thread,
                workers,
                gate,
                str(bound[0]),
                int(bound[1]),
                background_stop,
            )
        if not thread.is_alive():
            break
        time.sleep(0.05)
    # Stop the listener before the executor. A bind that completes just after
    # the deadline would otherwise keep serving Slack with no worker to run the
    # turn — every request 500s and Slack retries it, while the gateway has
    # already recorded Slack as unavailable.
    gate.accepting = False  # a thread that outlives the join must act on nothing
    server.should_exit = True
    thread.join(timeout=_FAILED_START_JOIN_SECONDS)
    if thread.is_alive():
        # Bounded wait, not an unbounded one: a listener that will not unwind
        # must not hold up the rest of the gateway. It is a daemon thread, so
        # it cannot outlive the process, and any delivery it still answers gets
        # a 503 from the executor guard above rather than a 500.
        logger.warning(
            "slack http listener did not stop within %ss after a failed start",
            _FAILED_START_JOIN_SECONDS,
        )
    workers.shutdown(wait=False, cancel_futures=True)
    if background_stop is not None:
        background_stop(0.0)
    # GatewayTransportFailedError, not RuntimeError: start_transports catches
    # this and records Slack unavailable instead of aborting the gateway.
    raise GatewayTransportFailedError(f"Slack HTTP listener on {host}:{port} failed to start")


__all__ = [
    "EVENTS_PATH",
    "ListenerGate",
    "INTERACTIVITY_PATH",
    "SlackHttpServerHandle",
    "build_slack_http_app",
    "serve_slack_http_in_thread",
]
