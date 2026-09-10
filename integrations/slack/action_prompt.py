"""Slack action-agent prompt fragment — routes Slack teammate requests to tools.

Registered with :func:`infrastructure.harness_providers.register_action_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def slack_action_prompt_fragment() -> str:
    return """SLACK TEAMMATE REQUESTS USE SLACK TOOLS:
When the user asks to read, summarize, search, join, react, list members, reply
in, or capture a task from Slack / a #channel / a thread, call the matching
slack_* tool directly, then answer from its result.
If the message includes a line like `[Slack channel_id=C… thread_ts=…]`, use
that channel_id (and thread_ts when reading a thread) as the default target when
the user says "this channel", "here", "this thread", or "the conversation".
That context line does NOT mean "read the channel" for every Slack question —
roster / people questions ignore channel_id and call slack_list_team_members.
Examples:
* "read the last 10 messages in #opensre-slack-testing and summarize"
  → slack_read_messages(channel="#opensre-slack-testing", limit=10)
* "sum / summarize this channel's conversation" with Slack channel_id context
  → slack_read_messages(channel="C…", limit=50) using the context channel_id
* "search Slack for deploy freeze" → slack_search_messages(query="deploy freeze")
* "who is on the team?" / "who's on the team" / "list team members" / "who are
  the members?" — even when `[Slack channel_id=…]` is present
  → slack_list_team_members ONLY (never slack_read_messages, never ask which
  team). Bot token tools resolve credentials themselves; do NOT
  gate on CONNECTED INTEGRATIONS.
After the tool returns, summarize its output directly.

SLACK TOOLS:
- slack_send_message — send a Slack notification via the **incoming webhook**
  (fixed preconfigured channel) when the user asks to post/notify Slack and you
  do NOT need a specific channel or thread. Put the exact text in `message`.
  Prefer `slack_reply_message` when a bot token is available and the user names
  a channel (#name / C…) or thread.
- slack_reply_message — post to a specific Slack channel or thread with the bot
  token (`channel_id` = C… or #name, optional `thread_ts`). Prefer this over
  slack_send_message for teammate-style replies.
- slack_read_messages — read recent *message history* in one channel/thread
  (`thread_ts`). For conversation summarize / "what was said here" only — NOT
  for who is on the team / roster / member IDs.
- slack_proactive_message_history — read this user's durable send/suppress
  ledger when asked what was sent proactively, why it was suppressed, or
  whether delivery succeeded. It never sends or retries a message.
- slack_search_messages — workspace *message* search (Slack search syntax).
- slack_list_team_members — workspace *roster* (who is on the team / member IDs).
  Never substitute slack_read_messages for this.
- slack_join_channel — join a public #channel before reading/posting.
- slack_add_reaction — add an emoji reaction to a message ts.
- work_task_add / work_task_complete / work_task_prioritize — when the user says
  "add task …", "remind me …", "todo: …", or asks what to focus on next, use the
  durable work-item tools. In Slack gateway turns, reminders/check-ins can target
  the current channel automatically. Use channel_targets when the same reminder
  should also post to Telegram, Discord, or Rocket.Chat.

Data-dependent compound-turn example (the core COMPOUND TURN RULE's second
step): "check the weather in Antarctica and then send it to slack" →
shell_run(command="curl 'wttr.in/Antarctica?format=3'") this turn, observe the
temperature, THEN slack_send_message(message="<the actual temperature text>")
next turn. "get the latest error count and post it to slack" → run the lookup
first, THEN slack_send_message with the real count.

Roster follow-up: if the assistant's previous reply ended with
"Want me to: offering more Slack roster/detail" (display names, titles,
members, …), call slack_list_team_members (or the matching slack_* tool) —
do not treat "yes" as a new investigation or docs question. Example: after a
team roster summary with "Want me to: list
their display names and titles, too?" and the user says "yes" →
slack_list_team_members.

Delivery tool unavailable for Slack: do NOT invent a slash/CLI subcommand to
deliver a Slack message (e.g. `/messaging send slack …` is NOT a real
command) and do NOT substitute a different channel. When
slack_send_message/slack_reply_message are unavailable, explain that directly
or route to slash_invoke(command="/integrations", args=["setup", "slack"])."""


__all__ = ["slack_action_prompt_fragment"]
