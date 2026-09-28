"""Guide Telegram's bot-creation and destination-selection hand-offs."""

from __future__ import annotations

from collections.abc import Mapping

from integrations.setup_flow import SetupUI
from integrations.telegram.setup_discovery import TelegramSetupError, discover_bot, discover_chats


def guide_telegram(ui: SetupUI, saved: Mapping[str, str]) -> dict[str, str]:
    """Explain the human steps, verify the bot, then let the user choose a reachable chat."""
    ui.step("one", "Create or connect your bot")
    ui.say("Open https://t.me/BotFather in Telegram.")
    ui.say("Send /newbot, choose a name and a username ending in bot, then copy the bot token.")
    ui.say("Already have a bot? Send /mybots, select your bot, then choose API Token.")
    token = ui.value(
        "Paste the bot token here (hidden)", default=saved.get("bot_token", ""), secret=True
    )
    while True:
        ui.say("Checking your bot… (up to 10 seconds)")
        try:
            username = discover_bot(token)
            break
        except TelegramSetupError as exc:
            ui.say(str(exc))
            action = ui.choose(
                "Next action",
                [("retry", "Retry"), ("edit", "Paste another token"), ("cancel", "Cancel")],
            )
            if action == "edit":
                token = ui.value("Paste the bot token here (hidden)", secret=True)

    ui.step("two", "Choose where messages should go")
    ui.say(f"For a private chat, open https://t.me/{username} and press Start.")
    ui.say(f"For a group, add @{username} and send /start@{username} in the group.")
    ui.say(
        "For a channel, add the bot as an administrator with permission to post, then publish a message."
    )
    ui.say("When you're ready, I'll find the chat so you don't need to look up an ID.")
    choices = [("discover", "I've done that — find my chat")]
    if saved.get("default_chat_id"):
        choices.append(("saved", f"Keep current chat ({saved['default_chat_id']})"))
    choices.extend([("manual", "Use a public @channelname or a chat ID"), ("cancel", "Cancel")])
    action = ui.choose("Ready?", choices)
    while True:
        if action == "saved":
            chat_id = saved["default_chat_id"]
            break
        if action == "manual":
            ui.say("For a public channel, use the @name from its Telegram profile.")
            ui.say(
                "For a private chat without an ID, go back and use Find my chat after pressing Start."
            )
            chat_id = ui.value("Public @channelname or numeric chat ID")
            break
        ui.say("Finding recent chats… (up to 20 seconds)")
        try:
            chats = discover_chats(token)
        except TelegramSetupError as exc:
            ui.say(str(exc))
            chats = []
        if not chats:
            ui.say("No chats found yet. Send a new message to the bot or group, then retry.")
        action = ui.choose(
            "Choose the chat OpenSRE should use",
            [
                *chats,
                ("discover", "I've sent a message — look again"),
                ("manual", "Enter a public @channelname or chat ID"),
                ("cancel", "Cancel"),
            ],
        )
        if action not in {"discover", "manual"}:
            chat_id = action
            break
    ui.say("I'll verify this bot can reach your selected chat. No test message will be sent.")
    return {"bot_token": token, "default_chat_id": chat_id}
