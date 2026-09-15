"""Registration steps a process opts into, one function per step.

``surfaces`` and ``gateway`` may not import each other, so each used to keep a
byte-identical copy of this registration and synchronise it by comment. It lives
here once; :mod:`bootstrap.process` composes the steps a profile asks for.

Imports stay inside the functions: registration pulls in the tool and
integration registries, and a host that only needs adapters should not pay for
the scheduler ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrastructure.scheduling.scheduler.delivery_bundle import ScheduledDeliveryAdapters
    from infrastructure.scheduling.scheduler.runners import SchedulerRunners


def install_harness_adapters() -> None:
    """Register the integration and tool adapters the harness resolves through.

    Without this a harness starts but no tool is available — the ports report
    nothing until both registries have been installed.
    """
    import infrastructure.harness_providers as harness_providers
    from integrations.harness_adapters import (
        register_harness_adapters as register_integrations,
    )
    from tools.harness_adapters import register_harness_adapters as register_tools
    from tools.interactive_shell.subprocess_presenter import (
        headless_subprocess_presenter_factory,
    )

    register_integrations()
    register_tools()
    harness_providers.SubprocessPresenterProvider(headless_subprocess_presenter_factory).install()
    # Shell / REPL / gateway slash surface: CTA must name a runnable command.
    harness_providers.IntegrationSetupCommand(
        lambda service_id: f"/integrations setup {service_id}"
    ).install()


def scheduler_runners() -> SchedulerRunners:
    """Assemble the runner scheduled tasks dispatch through.

    The only layer that may see both ``integrations`` and ``tools``, so the
    bundle is built here and handed to whichever host installs it.
    """
    from infrastructure.scheduling.scheduler.runners import SchedulerRunners
    from integrations.scheduled_agent_bootstrap import run_scheduled_agent_digest

    return SchedulerRunners(agent=run_scheduled_agent_digest)


def scheduled_delivery_adapters() -> ScheduledDeliveryAdapters:
    """Assemble the per-provider adapters scheduled delivery dispatches through.

    The only layer that may see both ``integrations`` and ``infrastructure``, so
    the vendor adapters are bundled here and handed to whichever host installs it.
    """
    from infrastructure.scheduling.scheduler.delivery_bundle import ScheduledDeliveryAdapters
    from infrastructure.scheduling.scheduler.interactive_shell_delivery import (
        InteractiveShellScheduledDelivery,
    )
    from infrastructure.scheduling.scheduler.types import Provider
    from integrations.buzz import BuzzScheduledDelivery
    from integrations.discord.scheduled_delivery import DiscordScheduledDelivery
    from integrations.rocketchat.scheduled_delivery import RocketChatScheduledDelivery
    from integrations.slack.scheduled_delivery import SlackScheduledDelivery
    from integrations.telegram.scheduled_delivery import TelegramScheduledDelivery

    return ScheduledDeliveryAdapters(
        {
            Provider.TELEGRAM: TelegramScheduledDelivery(),
            Provider.SLACK: SlackScheduledDelivery(),
            Provider.DISCORD: DiscordScheduledDelivery(),
            Provider.ROCKETCHAT: RocketChatScheduledDelivery(),
            Provider.INTERACTIVE_SHELL: InteractiveShellScheduledDelivery(),
            Provider.BUZZ: BuzzScheduledDelivery(),
        }
    )


def install_scheduled_delivery_adapters() -> None:
    """Bind the scheduled-delivery adapters (worker and CLI hosts)."""
    scheduled_delivery_adapters().install()


def install_cli_auth_checker() -> None:
    """Bind the integrations-backed CLI auth checker config reports status through.

    The integrations import is deferred to the first check, so a bare CLI
    invocation (e.g. ``opensre --help``) does not pay the subprocess-client cost.
    """
    from config.llm_auth.cli_auth import CliAuthChecker, CliAuthState

    def check(provider: str) -> CliAuthState | None:
        from integrations.llm_cli import check_cli_auth

        return check_cli_auth(provider)

    CliAuthChecker(check).install()


__all__ = [
    "install_harness_adapters",
    "scheduler_runners",
    "scheduled_delivery_adapters",
    "install_scheduled_delivery_adapters",
    "install_cli_auth_checker",
]
