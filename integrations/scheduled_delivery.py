"""Every provider's scheduled-delivery adapter, bundled for a host to install.

Each vendor owns its delivery under ``integrations/<vendor>/``. The scheduler in
``infrastructure`` may not import them, so it dispatches through the bundle a
host installs at boot (``BootStep.SCHEDULER_RUNNERS``) or when it starts the
scheduler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrastructure.scheduling.scheduler.delivery_bundle import ScheduledDeliveryAdapters


def scheduled_delivery_adapters() -> ScheduledDeliveryAdapters:
    """Assemble the per-provider adapters scheduled delivery dispatches through.

    Imports stay inside: each delivery pulls in its vendor's client, and a host
    that only registers harness adapters should not pay for them.
    """
    from infrastructure.scheduling.scheduler.delivery_bundle import ScheduledDeliveryAdapters
    from infrastructure.scheduling.scheduler.interactive_shell_delivery import (
        InteractiveShellScheduledDelivery,
    )
    from infrastructure.scheduling.scheduler.types import Provider
    from integrations.discord.scheduled_delivery import DiscordScheduledDelivery
    from integrations.rocketchat.scheduled_delivery import RocketChatScheduledDelivery
    from integrations.scheduled_email import EmailScheduledDelivery
    from integrations.slack.scheduled_delivery import SlackScheduledDelivery
    from integrations.telegram.scheduled_delivery import TelegramScheduledDelivery

    return ScheduledDeliveryAdapters(
        {
            Provider.TELEGRAM: TelegramScheduledDelivery(),
            Provider.SLACK: SlackScheduledDelivery(),
            Provider.DISCORD: DiscordScheduledDelivery(),
            Provider.ROCKETCHAT: RocketChatScheduledDelivery(),
            Provider.EMAIL: EmailScheduledDelivery(),
            Provider.INTERACTIVE_SHELL: InteractiveShellScheduledDelivery(),
        }
    )


def install_scheduled_delivery_adapters() -> None:
    """Bind the scheduled-delivery adapters for this process."""
    scheduled_delivery_adapters().install()


__all__ = ["install_scheduled_delivery_adapters", "scheduled_delivery_adapters"]
