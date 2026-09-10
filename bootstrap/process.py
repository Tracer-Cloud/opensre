"""Shared process boot for CLI, gateway, web, and scheduler hosts.

A profile names the steps it wants; this module owns the order they run in. One
ordered table rather than a branch per step, so adding a step is a table entry
and a profile cannot invent a different sequence.

Does **not** configure gateway or CLI logging — that stays with the surface
composition root (``GatewayController.configure_logging``, CLI stderr).

Does **not** construct :class:`~core.agent_harness.turns.headless_agent.HeadlessAgent`
or run turns — that is ``DefaultHeadlessBuild.agent`` /
:class:`~core.agent_harness.harness.AgentSession` after boot. Bootstrap and
headless construction are separate layers, not duplicated stacks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from bootstrap.adapters import (
    install_cli_auth_checker,
    install_harness_adapters,
    install_scheduled_delivery_adapters,
)
from config.local_env import bootstrap_opensre_env_once

_LOG = logging.getLogger(__name__)


class ProcessName(StrEnum):
    """The hosts that run shared process boot.

    A closed set: a host picks one of these rather than naming a new profile,
    which is what keeps the idempotency key enumerable.
    """

    CLI = "cli"
    GATEWAY = "gateway"
    WEB = "web"
    SCHEDULER_WORKER = "scheduler_worker"
    SCHEDULED_COMMAND = "scheduled_command"
    EMBEDDED = "embedded"


class SentryEntrypoint(StrEnum):
    """The ``entrypoint`` tag Sentry groups a process's events under."""

    CLI = "cli"
    GATEWAY = "gateway"
    WEBAPP = "webapp"
    SCHEDULER = "scheduler"


class BootStep(StrEnum):
    """A step a profile can opt into. Order lives in :data:`_STEP_ORDER`."""

    ENV = "env"
    SENTRY = "sentry"
    OTEL_TRACING = "otel_tracing"
    HARNESS_ADAPTERS = "harness_adapters"
    SCHEDULER_RUNNERS = "scheduler_runners"
    CAPABILITY_WARNINGS = "capability_warnings"
    PRELOAD_LLM = "preload_llm"


@dataclass(frozen=True, slots=True)
class ProcessProfile:
    """Which shared boot steps a host opts into."""

    name: ProcessName
    steps: frozenset[BootStep]
    sentry_entrypoint: SentryEntrypoint = SentryEntrypoint.CLI


# Fixed profiles — surfaces pick one; they do not invent parallel boot orders.
CLI_PROFILE: Final = ProcessProfile(
    name=ProcessName.CLI,
    # CLI owns Sentry (update tolerates a missing SDK) and Rich product adapters.
    steps=frozenset({BootStep.ENV, BootStep.OTEL_TRACING}),
)
GATEWAY_PROFILE: Final = ProcessProfile(
    name=ProcessName.GATEWAY,
    steps=frozenset(
        {
            BootStep.ENV,
            BootStep.SENTRY,
            BootStep.OTEL_TRACING,
            BootStep.HARNESS_ADAPTERS,
            BootStep.CAPABILITY_WARNINGS,
            BootStep.PRELOAD_LLM,
        }
    ),
    sentry_entrypoint=SentryEntrypoint.GATEWAY,
)
WEB_PROFILE: Final = ProcessProfile(
    name=ProcessName.WEB,
    steps=frozenset(
        {BootStep.ENV, BootStep.SENTRY, BootStep.OTEL_TRACING, BootStep.HARNESS_ADAPTERS}
    ),
    sentry_entrypoint=SentryEntrypoint.WEBAPP,
)
SCHEDULER_WORKER_PROFILE: Final = ProcessProfile(
    name=ProcessName.SCHEDULER_WORKER,
    # Dedicated blocking scheduler process (`opensre cron start`). Owns its
    # Sentry entrypoint and installs runners at boot. Gateway co-hosts the
    # scheduler differently: GATEWAY_PROFILE + late install_scheduler_runners
    # in GatewayController.start_scheduler — do not confuse the two.
    steps=frozenset(
        {
            BootStep.ENV,
            BootStep.SENTRY,
            BootStep.OTEL_TRACING,
            BootStep.HARNESS_ADAPTERS,
            BootStep.SCHEDULER_RUNNERS,
        }
    ),
    sentry_entrypoint=SentryEntrypoint.SCHEDULER,
)
SCHEDULED_COMMAND_PROFILE: Final = ProcessProfile(
    name=ProcessName.SCHEDULED_COMMAND,
    # A CLI command that creates or dispatches scheduled work (cron add, digests,
    # metric reports). It runs inside an already-booted CLI process, so Sentry
    # stays with the CLI; it needs the runners scheduled tasks dispatch through.
    # Not the long-running daemon — that is SCHEDULER_WORKER_PROFILE.
    steps=frozenset({BootStep.ENV, BootStep.HARNESS_ADAPTERS, BootStep.SCHEDULER_RUNNERS}),
)
EMBEDDED_PROFILE: Final = ProcessProfile(
    name=ProcessName.EMBEDDED,
    # Driving the agent from Python inside someone else's process: register the
    # adapters tools resolve through, and leave error reporting, scheduling and
    # client preloading to the host.
    steps=frozenset({BootStep.ENV, BootStep.HARNESS_ADAPTERS}),
)


def _run_env(_profile: ProcessProfile, _log: logging.Logger) -> None:
    bootstrap_opensre_env_once(override=False)


def _run_sentry(profile: ProcessProfile, _log: logging.Logger) -> None:
    from infrastructure.observability.errors.sentry import init_sentry

    init_sentry(entrypoint=profile.sentry_entrypoint)


def _run_otel_tracing(_profile: ProcessProfile, _log: logging.Logger) -> None:
    from infrastructure.observability.trace.otel_sdk import init_otel_tracing

    init_otel_tracing()


def _run_harness_adapters(_profile: ProcessProfile, _log: logging.Logger) -> None:
    install_harness_adapters()
    install_cli_auth_checker()


def _run_scheduler_runners(_profile: ProcessProfile, _log: logging.Logger) -> None:
    # The agent runners are now built at the scheduler-start call
    # site and passed in (no global); this step only installs delivery adapters.
    install_scheduled_delivery_adapters()


def _run_capability_warnings(profile: ProcessProfile, log: logging.Logger) -> None:
    from infrastructure.safety.sandbox.capabilities import boot_capability_warnings

    for warning in boot_capability_warnings():
        log.warning("[%s] capability: %s", profile.name, warning)


def _run_preload_llm(_profile: ProcessProfile, _log: logging.Logger) -> None:
    from core.llm.internal.preload import preload_llm_clients

    preload_llm_clients()


#: The one boot sequence. Membership in ``profile.steps`` selects; this tuple
#: decides order, so no profile can run adapters before the environment loads.
_STEP_ORDER: Final[
    tuple[tuple[BootStep, Callable[[ProcessProfile, logging.Logger], None]], ...]
] = (
    (BootStep.ENV, _run_env),
    (BootStep.SENTRY, _run_sentry),
    # Before adapters and any client construction, so outbound calls a later
    # step makes are already covered by requests/botocore instrumentation.
    (BootStep.OTEL_TRACING, _run_otel_tracing),
    (BootStep.HARNESS_ADAPTERS, _run_harness_adapters),
    (BootStep.SCHEDULER_RUNNERS, _run_scheduler_runners),
    (BootStep.CAPABILITY_WARNINGS, _run_capability_warnings),
    (BootStep.PRELOAD_LLM, _run_preload_llm),
)

_configured_profiles: set[ProcessName] = set()


def configure_process(
    profile: ProcessProfile,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Run the steps ``profile`` opted into, in the shared order. Idempotent.

    Logging configuration is the caller's responsibility.
    """
    if profile.name in _configured_profiles:
        return
    log = logger or _LOG
    for step, run in _STEP_ORDER:
        if step in profile.steps:
            run(profile, log)
    _configured_profiles.add(profile.name)


def reset_process_runtime_for_tests() -> None:
    """Clear per-profile idempotency so characterization tests can re-boot.

    Production has no reason to un-boot; tests across packages call this
    instead of poking ``_configured_profiles``.
    """
    _configured_profiles.clear()


__all__ = [
    "BootStep",
    "CLI_PROFILE",
    "EMBEDDED_PROFILE",
    "GATEWAY_PROFILE",
    "ProcessName",
    "ProcessProfile",
    "SCHEDULED_COMMAND_PROFILE",
    "SCHEDULER_WORKER_PROFILE",
    "SentryEntrypoint",
    "WEB_PROFILE",
    "configure_process",
    "reset_process_runtime_for_tests",
]
