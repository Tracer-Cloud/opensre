"""Start and stop everything the gateway serves users through.

:func:`start_gateway` brings up the web server and every chat transport and
returns the running handle. Each transport starts its own worker; this module
only composes those starts.

Only :class:`~gateway.core.lifecycle.controller.GatewayController` imports this
module, and only this module imports ``gateway.transports.startup``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config.constants.gateway import (
    DEFAULT_STOP_TIMEOUT_SECONDS,
    PROMPT_WORKER_STOP_TIMEOUT_SECONDS,
    WEB_STOP_TIMEOUT_SECONDS,
)
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.core.prompt_intake import PromptQueue, PromptTurnRunner, PromptWorker
from gateway.transports.names import TransportName
from gateway.transports.startup import (
    TransportHandle,
    start_transports,
    stop_transports,
)
from gateway.web.startup import start_web_server
from gateway.web.web_server import WebAppServerHandle
from infrastructure.turn_host.turn_callback import TurnCallback

_WEB_COMPONENT = "web"
_PROMPT_COMPONENT = "remote prompts"


@dataclass
class StartedGateway:
    """The running gateway: optional web server, chat transports, statuses."""

    web_server: WebAppServerHandle | None = None
    transports: dict[TransportName, TransportHandle] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    prompt_worker: PromptWorker | None = None

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        """Stop web and every chat transport; return whether all chat workers stopped."""
        budget = ShutdownBudget(timeout)
        if self.prompt_worker is not None:
            started = budget.mark()
            self.prompt_worker.stop(timeout_seconds=budget.take(PROMPT_WORKER_STOP_TIMEOUT_SECONDS))
            budget.consume(started)
            self.prompt_worker = None
        if self.web_server is not None:
            started = budget.mark()
            self.web_server.stop(timeout=budget.take(WEB_STOP_TIMEOUT_SECONDS))
            budget.consume(started)
            self.web_server = None
        stopped = stop_transports(handles=list(self.transports.values()), timeout=budget.remaining)
        self.transports = {}
        return stopped


def start_gateway(
    *,
    logger: logging.Logger,
    handler: TurnCallback,
    prompt_runner: PromptTurnRunner | None = None,
) -> StartedGateway:
    """Start web and every chat transport together.

    Missing chat credentials skip that transport (``not configured``); readiness
    or runtime failures record ``failed``. The rest still start. Remote prompts
    are accepted only when ``prompt_runner`` is given.
    """
    prompt_worker = None
    statuses: dict[str, str] = {}
    if prompt_runner is not None:
        prompt_worker = start_prompt_intake(logger=logger, runner=prompt_runner)
        statuses[_PROMPT_COMPONENT] = "accepting"
    web = start_web_server(logger=logger)
    chat = start_transports(logger=logger, handler=handler)
    statuses[_WEB_COMPONENT] = web.status
    for name, status in chat.statuses.items():
        statuses[name] = status
    return StartedGateway(
        web_server=web.server,
        transports={handle.name: handle for handle in chat.handles},
        statuses=statuses,
        prompt_worker=prompt_worker,
    )


def start_prompt_intake(*, logger: logging.Logger, runner: PromptTurnRunner) -> PromptWorker:
    """Attach a prompt queue to the web app and start the thread that runs its jobs."""
    from gateway.web.webapp import app

    queue = PromptQueue()
    app.state.prompt_queue = queue
    worker = PromptWorker(queue, runner, logger=logger)
    worker.start()
    return worker


__all__ = [
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "WEB_STOP_TIMEOUT_SECONDS",
    "StartedGateway",
    "start_gateway",
]
