"""Configurator handlers for chat-bot notification channels."""

from __future__ import annotations

from infrastructure.terminal.theme import SECONDARY
from integrations.buzz import BUZZ_SETUP
from integrations.discord import DISCORD_SETUP
from integrations.rocketchat import ROCKETCHAT_SETUP
from integrations.slack import SLACK_SETUP
from integrations.telegram.setup import TELEGRAM_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_slack() -> tuple[str, str]:
    return configure_from_spec(
        SLACK_SETUP,
        title="Slack",
        intro=(
            "\n[bold]Slack Integration[/bold]\n"
            "Provide a webhook URL for outbound delivery, Socket Mode tokens "
            "(xoxb- + xapp-) for two-way gateway chat, or both.\n"
        ),
    )


def _configure_discord() -> tuple[str, str]:
    return configure_from_spec(
        DISCORD_SETUP,
        title="Discord",
        intro=(
            "\n[bold]Discord Integration[/bold]\n"
            f"[{SECONDARY}]Get your credentials from https://discord.com/developers/applications. "
            "Only the bot token is required; the application ID, public key, and a "
            "default channel ID are optional.[/]\n"
        ),
    )


def _configure_rocketchat() -> tuple[str, str]:
    return configure_from_spec(
        ROCKETCHAT_SETUP,
        title="Rocket.Chat",
        intro=(
            "\n[bold]Rocket.Chat Integration[/bold]\n"
            f"[{SECONDARY}]Set it up one of two ways: a personal access token (server URL + "
            "token + user ID, for dynamic channel targeting) or an incoming webhook (a fixed "
            "destination). Leave the fields for the path you are not using blank.\n"
            "Personal Access Token: My Account > Personal Access Tokens (the token page also "
            "shows your user ID). Incoming webhook: Administration > Integrations > Incoming.[/]\n"
        ),
    )


def _configure_buzz() -> tuple[str, str]:
    return configure_from_spec(
        BUZZ_SETUP,
        title="Buzz",
        intro=(
            "\n[bold]Buzz Integration[/bold]\n"
            f"[{SECONDARY}]Buzz (block/buzz) is a self-hostable, Nostr-based workspace. "
            "Generate an agent identity with `buzz-admin generate-key`, then paste its "
            "private key here (kept out of plain .env — stored in ~/.opensre/credentials.json). "
            "Channels are UUIDs — run `buzz channels list` to find one.\n"
            "Requires the `buzz` CLI on PATH (`cargo install --path crates/buzz-cli` from "
            "https://github.com/block/buzz). Press Ctrl+C to skip Buzz setup; "
            "`opensre integrations setup buzz` picks it up later.[/]\n"
        ),
    )


def _configure_telegram() -> tuple[str, str]:
    return configure_from_spec(
        TELEGRAM_SETUP,
        title="Telegram",
        intro=(
            "\n[bold]Telegram Integration[/bold]\n"
            f"[{SECONDARY}]Create a bot with @BotFather, then add it to the chat it should post "
            "in. For a public channel the @name is enough; otherwise find the numeric chat id "
            "via getUpdates. See docs/integrations/messaging/telegram for details.\n"
            "Both answers are required — Telegram cannot deliver without a chat. Press Ctrl+C to "
            "skip Telegram setup; `opensre integrations setup telegram` picks it up later.[/]\n"
        ),
    )
