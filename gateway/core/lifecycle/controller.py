"""Gateway process entrypoint and lifecycle owner.

``GatewayController`` boots the background agent: logging, credentials,
:func:`bootstrap.process.configure_process` (``GATEWAY_PROFILE``), then one
turn runner and the components that use it.

* :meth:`start_surfaces` starts web and chat transports together
* :meth:`start_scheduler` hosts the process-wide cron/loop runner

Owns signals and ``stop``/``wait``. Component state is written through
:func:`gateway.core.process.component_status.write_component_status`.
This class does not start individual workers or execute turns.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable
from typing import Any

from rich.console import Console

from config.constants.gateway import (
    DEFAULT_STOP_TIMEOUT_SECONDS,
    SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS,
)
from core.agent_harness.ports import SlashPortsFactory
from gateway import startup as gateway_startup
from gateway.core.billing.turn_metering import admit_metered_turn
from gateway.core.chat_agent_build import chat_agent_build_config
from gateway.core.config.logging_config import configure_logging
from gateway.core.lifecycle.credential_hydration import (
    GatewayBootstrap,
    GatewayCredentialHydrator,
)
from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.core.process.component_status import clear_component_status, write_component_status
from gateway.core.process.readiness import set_ready
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.core.process.supervision import GATEWAY_PID_FILE
from infrastructure.turn_host.concurrency import (
    TurnConcurrencyGate,
    process_turn_gate,
    set_process_turn_gate,
)
from infrastructure.turn_host.turn_callback import TurnCallback
from infrastructure.turn_host.turn_runner import TurnRunner

CredentialHydratorFactory = Callable[[], GatewayCredentialHydrator | None]


def _gateway_hosts_scheduler() -> bool:
    """Whether this gateway process co-hosts the scheduler loop (default true).

    Set ``OPENSRE_GATEWAY_HOST_SCHEDULER`` false to run the scheduler as its own
    service (``MODE=scheduler``) so scheduled tasks are not fired by two processes.
    """
    from config.constants.scheduler import OPENSRE_GATEWAY_HOST_SCHEDULER_ENV

    value = os.getenv(OPENSRE_GATEWAY_HOST_SCHEDULER_ENV)
    return value is None or value.strip().lower() not in {"0", "false", "no", "off"}


class GatewayController:
    """Composition root and lifecycle handle for the running gateway process."""

    def __init__(
        self,
        *,
        slash_ports_factory: SlashPortsFactory | None = None,
        credential_hydrator_factory: CredentialHydratorFactory | None = None,
        turn_gate: TurnConcurrencyGate | None = None,
    ) -> None:
        self.logger: logging.Logger | None = None
        self.surfaces: gateway_startup.StartedGateway | None = None
        self.scheduler: Any = None
        self._scheduler_runners: Any = None
        self._scheduler_reload_thread: threading.Thread | None = None
        self.components: dict[str, str] = {}
        self._slash_ports_factory = slash_ports_factory
        self._credential_hydrator_factory = (
            credential_hydrator_factory or GatewayCredentialHydrator.from_environment
        )
        if turn_gate is not None:
            set_process_turn_gate(turn_gate)
            self.turn_gate = turn_gate
        else:
            self.turn_gate = process_turn_gate()
        self._stopped = threading.Event()

    def start_gateway(self, *, wait: bool = True) -> GatewayController:
        """Credential hydrate, shared process boot, then channels + scheduler."""
        from bootstrap.process import GATEWAY_PROFILE, configure_process

        logger = self.logger = configure_logging()
        set_ready(False)
        self._load_credentials(logger)
        configure_process(GATEWAY_PROFILE, logger=logger)

        # One turn runner for every chat transport. Capacity gate lives on the
        # same object — do not wrap it in a second "turn runner". Action tools
        # resolve per turn from each chat's live session inside the handler.
        console = Console(force_terminal=False)
        handler = TurnRunner(
            console=console,
            slash_ports_factory=self._slash_ports_factory,
            agent_build=chat_agent_build_config(),
            gate=self.turn_gate,
            admission_check=admit_metered_turn,
        )

        self.start_surfaces(logger=logger, handler=handler)
        if _gateway_hosts_scheduler():
            self.start_scheduler(logger=logger)
        else:
            self.components["scheduler"] = "external (dedicated MODE=scheduler service)"
            logger.info("[gateway] in-process scheduler disabled; run it as its own service")
        self._publish_status(logger)
        # Deploy health waits (EC2 Docker + AMI) match this line for Telegram
        # and/or Slack — do not rely on transport-specific log strings alone.
        logger.info("[gateway] ready")
        set_ready(True)

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        if wait:
            self.wait()
        return self

    def start_surfaces(
        self,
        *,
        logger: logging.Logger,
        handler: TurnCallback,
    ) -> None:
        """Start web + every chat transport together (via :mod:`gateway.startup`)."""
        self.surfaces = gateway_startup.start_gateway(logger=logger, handler=handler)
        self.components.update(self.surfaces.statuses)

    def start_scheduler(self, *, logger: logging.Logger) -> None:
        """Host ``infrastructure.scheduling.scheduler`` here — runners and APScheduler stay there.

        Not a gateway surface. CLI/shell mutate the task store and call
        :func:`request_scheduler_reload`; this process only installs gated
        runners and starts :func:`start_background_scheduler`.
        """
        from bootstrap.adapters import install_scheduled_delivery_adapters, scheduler_runners
        from infrastructure.scheduling.scheduler.reload_signal import (
            consume_scheduler_reload_request,
        )
        from infrastructure.scheduling.scheduler.runner import start_background_scheduler

        # Multiplexed scheduled-agent runners (Sentry digest, etc.).
        # A scheduled run costs a turn, so it takes the same capacity gate chat
        # turns take — stated here, once, and passed into the scheduler.
        self._scheduler_runners = scheduler_runners().gated(self.turn_gate)
        install_scheduled_delivery_adapters()
        # Drop any reload request queued before this process owned the scheduler.
        consume_scheduler_reload_request()
        scheduler, task_count = start_background_scheduler(self._scheduler_runners)
        if scheduler is None:
            self.components["scheduler"] = "idle (no scheduled tasks)"
        else:
            self.scheduler = scheduler
            self.components["scheduler"] = f"running {task_count} scheduled task(s)"
        self._start_scheduler_reload_watcher(logger)

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        """Shut down all components and return whether the chat workers stopped."""
        budget = ShutdownBudget(timeout)
        set_ready(False)
        self._stopped.set()
        stopped = True
        if self._scheduler_reload_thread is not None:
            started = budget.mark()
            self._scheduler_reload_thread.join(
                timeout=budget.take(SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS)
            )
            budget.consume(started)
            self._scheduler_reload_thread = None
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        if self.surfaces is not None:
            stopped = self.surfaces.stop(timeout=budget.remaining) and stopped
            self.surfaces = None
        clear_component_status()
        return stopped

    def _load_credentials(self, logger: logging.Logger) -> GatewayBootstrap | None:
        """Hydrate before any transport, scheduler, or worker can start."""
        try:
            hydrator = self._credential_hydrator_factory()
            if hydrator is None:
                self.components["credentials"] = "not configured"
                return None
            bootstrap = hydrator.hydrate()
        except Exception as exc:
            logger.error("gateway credential hydration failed (%s)", type(exc).__name__)
            self.components["credentials"] = "failed"
            raise GatewayConfigurationError("Gateway credential hydration failed") from None
        self.components["credentials"] = (
            "hydrated" if bootstrap.integrations_hydrated else "preseeded"
        )
        return bootstrap

    def wait(self, *, timeout: float | None = None) -> bool:
        """Wait until shutdown is requested and return whether the gateway has stopped."""
        return self._stopped.wait(timeout)

    def _start_scheduler_reload_watcher(self, logger: logging.Logger) -> None:
        """Keep the co-hosted scheduler in sync with cron / `/loops` mutations.

        Uses the shared watcher (reload signal + store-file reconcile), so a
        dropped best-effort signal still converges on the next poll.
        """
        if self._scheduler_reload_thread is not None:
            return

        def _watch() -> None:
            from infrastructure.scheduling.scheduler.reload_signal import watch_and_reconcile
            from infrastructure.scheduling.scheduler.storage import default_task_store_path

            watch_and_reconcile(
                self._stopped,
                lambda: self._reload_scheduler(logger),
                default_task_store_path(),
                on_error=lambda exc: logger.warning(
                    "Scheduler reload failed (%s)", type(exc).__name__
                ),
            )

        self._scheduler_reload_thread = threading.Thread(
            target=_watch,
            name="opensre-scheduler-reload",
            daemon=True,
        )
        self._scheduler_reload_thread.start()

    def _reload_scheduler(self, logger: logging.Logger) -> None:
        """Resync the live scheduler (or start one) from the current task store."""
        from infrastructure.scheduling.scheduler.runner import refresh_background_scheduler

        scheduler, task_count = refresh_background_scheduler(
            self.scheduler, self._scheduler_runners
        )
        self.scheduler = scheduler
        if scheduler is None:
            self.components["scheduler"] = "idle (no scheduled tasks)"
            logger.info("Scheduler idle after reload (no enabled tasks)")
        else:
            self.components["scheduler"] = f"running {task_count} scheduled task(s)"
            logger.info("Scheduler reloaded with %d task(s)", task_count)
        self._publish_status(logger)

    def _publish_status(self, logger: logging.Logger) -> None:
        GATEWAY_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        GATEWAY_PID_FILE.write_text(f"{os.getpid()}\n")
        write_component_status(self.components)
        for name, detail in self.components.items():
            logger.info("component %s: %s", name, detail)

    def _handle_signal(self, *_args: object) -> None:
        self.stop()


_BARE_MANAGER_EXIT = (
    "GatewayController requires slash_ports_factory for production chat.\n"
    "Start with: opensre gateway start\n"
    "        or: opensre gateway start --foreground\n"
    "Unit tests may construct GatewayController(...) directly.\n"
)


def start_gateway(
    *,
    wait: bool = True,
    slash_ports_factory: SlashPortsFactory | None = None,
) -> GatewayController:
    """Compatibility wrapper — requires ``slash_ports_factory`` (fail closed).

    Production boot goes through the CLI composition root
    (``opensre gateway start``), which injects headless slash ports. The
    gateway package must not import the surfaces layer, so bare
    ``GatewayController()`` here cannot inject them.
    """
    if slash_ports_factory is None:
        raise SystemExit(_BARE_MANAGER_EXIT)
    return GatewayController(slash_ports_factory=slash_ports_factory).start_gateway(wait=wait)


def main() -> None:
    """Refuse bare controller main — same policy as ``python -m gateway``."""
    raise SystemExit(_BARE_MANAGER_EXIT)


if __name__ == "__main__":
    main()
