"""Where OpenSRE Analytics may be shown: internal Slack conversations and the local CLI.

The figures are Tracer's own product analytics, for the OpenSRE team only. A
conversation that people outside the team can read (a Slack Connect channel
shared with another organization, Telegram, Discord or any other chat) never
receives them. Every check fails closed: a conversation that cannot be
confirmed internal is treated as external.
"""

from __future__ import annotations

from infrastructure.scheduling.scheduler.run_activity import current_run_activity
from infrastructure.scheduling.scheduler.types import Provider
from integrations.slack.web_client import conversation_externally_shared, resolve_bot_token

_SLACK = Provider.SLACK.value
_INTERNAL_ONLY = "OpenSRE Analytics is internal to the OpenSRE team"
_SCHEDULED_ONLY_BY_REPORT = (
    f"{_INTERNAL_ONLY}; on a schedule it is posted only by the daily growth report, "
    "which checks where it posts. Ask to schedule the growth report instead."
)


def audience_refusal(platform: str, chat_id: str) -> str | None:
    """Why analytics must not be shown in this conversation, or None when it may.

    ``platform`` is empty for the local CLI or shell, which only the token's
    holder can run; otherwise it is the chat the turn or delivery belongs to.
    """
    provider = platform.strip().lower()
    if not provider:
        return None
    if provider != _SLACK:
        return f"{_INTERNAL_ONLY} and is only shared in your internal Slack, not on {provider}."
    conversation = chat_id.strip()
    if not conversation:
        return f"{_INTERNAL_ONLY}; I could not tell which Slack conversation this is."
    target, token_error = resolve_bot_token()
    if target is None:
        return (
            f"{_INTERNAL_ONLY}; I could not confirm this conversation is internal ({token_error})."
        )
    shared, error = conversation_externally_shared(target, conversation)
    if shared is None:
        return f"{_INTERNAL_ONLY}; I could not confirm this conversation is internal ({error})."
    if shared:
        return (
            f"{_INTERNAL_ONLY} and is never shared in a channel connected to another organization."
        )
    return None


def turn_refusal(platform: str, chat_id: str) -> str | None:
    """``audience_refusal`` for a tool called in a model turn.

    A scheduled turn carries no chat stamp and its reply goes to whatever
    channel the loop names, which a tool cannot see; so inside a scheduled run
    the tools refuse and only the growth report builder, which checks its own
    destination, posts analytics on a schedule.
    """
    if current_run_activity() is not None:
        return _SCHEDULED_ONLY_BY_REPORT
    return audience_refusal(platform, chat_id)


__all__ = ["audience_refusal", "turn_refusal"]
