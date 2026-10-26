"""Slack conversational-assistant prompt fragment — channel-context exception.

Registered with :func:`infrastructure.harness_providers.register_assistant_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def slack_assistant_prompt_fragment() -> str:
    return (
        "Slack channel-context exception: do NOT ask for the target system, "
        "service, or alert context (the core vague-operational-question "
        "default) when the user already named a Slack #channel / channel_id, "
        "or when a [Slack channel_id=…] context line is present — answer from "
        "Slack tools or say what blocked the Slack read, without asking to run "
        "`/integrations setup`."
    )


__all__ = ["slack_assistant_prompt_fragment"]
