"""Shared process boot for CLI, gateway, web, scheduler, and embedded hosts.

A profile names the steps it wants; this module owns the order they run in. One
ordered table rather than a branch per step, so adding a step is a table entry
and a profile cannot invent a different sequence.

Two steps install what ``tools`` and ``integrations`` provide, and this tier may
import neither. The host that boots — ``surfaces`` or ``gateway``, the only
packages that may import both — passes that registration in as
:class:`CapabilityRegistrars`; the table here still decides when it runs.

Does **not** configure gateway or CLI logging — that stays with the host
(``GatewayController.configure_logging``, CLI stderr).

Does **not** construct :class:`~core.agent_harness.turns.headless_agent.HeadlessAgent`
or run turns — that is ``DefaultHeadlessBuild.agent`` /
:class:`~core.agent_harness.harness.AgentSession` after boot. Process boot and
headless construction are separate layers, not duplicated stacks.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from config.frozen_ca_bundle import use_bundled_ca_certificates
from config.local_env import bootstrap_opensre_env_once

_LOG = logging.getLogger(__name__)
_SKILLS_PULL_BOOT_DELAY_SECONDS = 3.0


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
    HARNESS_ADAPTERS = "harness_adapters"
    SCHEDULER_RUNNERS = "scheduler_runners"
    CAPABILITY_WARNINGS = "capability_warnings"
    PRELOAD_LLM = "preload_llm"
    SKILLS_PULL = "skills_pull"


@dataclass(frozen=True, slots=True)
class ProcessProfile:
    """Which shared boot steps a host opts into."""

    name: ProcessName
    steps: frozenset[BootStep]
    sentry_entrypoint: SentryEntrypoint = SentryEntrypoint.CLI


@dataclass(frozen=True, slots=True)
class CapabilityRegistrars:
    """What ``tools`` and ``integrations`` install, named by the host that boots.

    Each registrar is idempotent: a process that boots two profiles installs the
    same callables twice.
    """

    #: ``integrations``' harness adapters: integration resolution, prompt
    #: fragments, CLI LLM backends, the setup command, loop owners.
    integration_adapters: Callable[[], None]
    #: ``tools``' harness adapters: tool registry, skill tools, subprocess presenter.
    tool_adapters: Callable[[], None]
    #: The CLI-auth prober ``config`` reports installed CLI providers through.
    cli_auth_checker: Callable[[], None]
    #: The per-provider adapters scheduled delivery dispatches through.
    scheduled_delivery: Callable[[], None]

    def install_harness_adapters(self) -> None:
        """Register both capability packages' adapters into the harness ports.

        Without this a harness starts but no tool is available — the ports
        report nothing until both registries have been installed.
        """
        self.integration_adapters()
        self.tool_adapters()


# Fixed profiles — surfaces pick one; they do not invent parallel boot orders.
CLI_PROFILE: Final = ProcessProfile(
    name=ProcessName.CLI,
    # CLI owns Sentry (update tolerates a missing SDK) and Rich product adapters.
    steps=frozenset({BootStep.ENV, BootStep.SKILLS_PULL}),
)
GATEWAY_PROFILE: Final = ProcessProfile(
    name=ProcessName.GATEWAY,
    steps=frozenset(
        {
            BootStep.ENV,
            BootStep.SENTRY,
            BootStep.HARNESS_ADAPTERS,
            BootStep.CAPABILITY_WARNINGS,
            BootStep.PRELOAD_LLM,
            BootStep.SKILLS_PULL,
        }
    ),
    sentry_entrypoint=SentryEntrypoint.GATEWAY,
)
WEB_PROFILE: Final = ProcessProfile(
    name=ProcessName.WEB,
    steps=frozenset({BootStep.ENV, BootStep.SENTRY, BootStep.HARNESS_ADAPTERS}),
    sentry_entrypoint=SentryEntrypoint.WEBAPP,
)
SCHEDULER_WORKER_PROFILE: Final = ProcessProfile(
    name=ProcessName.SCHEDULER_WORKER,
    # Dedicated blocking scheduler process (`opensre cron start`). Owns its
    # Sentry entrypoint and installs runners at boot. Gateway co-hosts the
    # scheduler differently: GATEWAY_PROFILE + late install_scheduled_delivery_adapters
    # in GatewayController.start_scheduler — do not confuse the two.
    steps=frozenset(
        {
            BootStep.ENV,
            BootStep.SENTRY,
            BootStep.HARNESS_ADAPTERS,
            BootStep.SCHEDULER_RUNNERS,
            BootStep.SKILLS_PULL,
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
    # adapters tools resolve through, and leave error reporting, LLM tracing,
    # scheduling and client preloading to the host.
    steps=frozenset({BootStep.ENV, BootStep.HARNESS_ADAPTERS}),
)


def _run_env(
    _profile: ProcessProfile, _registrars: CapabilityRegistrars, _log: logging.Logger
) -> None:
    bootstrap_opensre_env_once(override=False)
    # After the env files, so a CA bundle configured in one is kept.
    use_bundled_ca_certificates()


def _run_sentry(
    profile: ProcessProfile, _registrars: CapabilityRegistrars, _log: logging.Logger
) -> None:
    from infrastructure.observability.errors.sentry import init_sentry

    init_sentry(entrypoint=profile.sentry_entrypoint)


def _run_harness_adapters(
    _profile: ProcessProfile, registrars: CapabilityRegistrars, _log: logging.Logger
) -> None:
    registrars.install_harness_adapters()
    registrars.cli_auth_checker()


def _run_scheduler_runners(
    _profile: ProcessProfile, registrars: CapabilityRegistrars, _log: logging.Logger
) -> None:
    # The agent runners are built at the scheduler-start call site and passed
    # in (no global); this step only installs delivery adapters.
    registrars.scheduled_delivery()


def _run_capability_warnings(
    profile: ProcessProfile, _registrars: CapabilityRegistrars, log: logging.Logger
) -> None:
    from infrastructure.safety.sandbox.capabilities import boot_capability_warnings

    for warning in boot_capability_warnings():
        log.warning("[%s] capability: %s", profile.name, warning)


def _run_preload_llm(
    _profile: ProcessProfile, _registrars: CapabilityRegistrars, _log: logging.Logger
) -> None:
    from core.llm.internal.preload import preload_llm_clients

    preload_llm_clients()


def _run_skills_pull(
    _profile: ProcessProfile, _registrars: CapabilityRegistrars, _log: logging.Logger
) -> None:
    from config.skills_auto_update import skills_auto_update_enabled

    if skills_auto_update_enabled():
        threading.Thread(target=_start_skills_pull, name="opensre-skills-boot", daemon=True).start()


def _start_skills_pull() -> None:
    # Wait out startup before importing the catalog: short commands exit first,
    # and the first turn never competes with the pull. A pulled release applies
    # at the next turn, never mid-turn.
    time.sleep(_SKILLS_PULL_BOOT_DELAY_SECONDS)
    from infrastructure.skills_registry import (
        install_skills_activation_telemetry,
        start_skills_puller,
    )

    install_skills_activation_telemetry()
    start_skills_puller()


#: The one boot sequence. Membership in ``profile.steps`` selects; this tuple
#: decides order, so no profile can run adapters before the environment loads.
_STEP_ORDER: Final[
    tuple[
        tuple[
            BootStep,
            Callable[[ProcessProfile, CapabilityRegistrars, logging.Logger], None],
        ],
        ...,
    ]
] = (
    (BootStep.ENV, _run_env),
    (BootStep.SENTRY, _run_sentry),
    (BootStep.HARNESS_ADAPTERS, _run_harness_adapters),
    (BootStep.SCHEDULER_RUNNERS, _run_scheduler_runners),
    (BootStep.CAPABILITY_WARNINGS, _run_capability_warnings),
    (BootStep.PRELOAD_LLM, _run_preload_llm),
    (BootStep.SKILLS_PULL, _run_skills_pull),
)

_configured_profiles: set[ProcessName] = set()


def boot_process(
    profile: ProcessProfile,
    registrars: CapabilityRegistrars,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Run the steps ``profile`` opted into, in the shared order. Idempotent per profile.

    Hosts call their own ``configure_process``, which supplies ``registrars``.
    Logging configuration is the caller's responsibility.
    """
    if profile.name in _configured_profiles:
        return
    log = logger or _LOG
    for step, run in _STEP_ORDER:
        if step in profile.steps:
            run(profile, registrars, log)
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
    "CapabilityRegistrars",
    "EMBEDDED_PROFILE",
    "GATEWAY_PROFILE",
    "ProcessName",
    "ProcessProfile",
    "SCHEDULED_COMMAND_PROFILE",
    "SCHEDULER_WORKER_PROFILE",
    "SentryEntrypoint",
    "WEB_PROFILE",
    "boot_process",
    "reset_process_runtime_for_tests",
]
