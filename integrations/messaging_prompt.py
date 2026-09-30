"""Action-agent prompt fragment shared by every chat delivery integration.

Vendor routing lives on each vendor's tool descriptions, which reach the model
only when that integration is connected. This fragment keeps the one rule that
must hold when the named channel's tool is absent.
"""

from __future__ import annotations


def messaging_action_prompt_fragment() -> str:
    return """MESSAGING DELIVERY:
Send to a chat channel (Slack, Telegram, Rocket.Chat, Buzz) only with that
channel's own send tool, and only when the user asks. If the tool for the
channel they name is not in your tool list, say it is not connected and offer
slash_invoke(command="/integrations", args=["setup", "<channel>"]). Never invent
a command to deliver the message, and never send it through a different channel."""


__all__ = ["messaging_action_prompt_fragment"]
