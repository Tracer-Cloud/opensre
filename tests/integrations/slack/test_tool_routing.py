"""Slack routing rules ride on the Slack tools, so they reach the model only when Slack is connected."""

from __future__ import annotations

from integrations.slack.tools.slack_list_members_tool.tool import SlackListTeamMembersTool
from integrations.slack.tools.slack_read_messages_tool.tool import SlackReadMessagesTool
from integrations.slack.tools.slack_send_message_tool.tool import SlackSendMessageTool


def test_slack_routing_rules_live_on_the_tools_that_need_them() -> None:
    roster = SlackListTeamMembersTool.description
    assert "ONLY tool for who is on the team" in roster
    assert "says yes to an offer of more roster detail" in roster

    read = SlackReadMessagesTool.description
    assert "slack_list_team_members instead" in read
    assert '"this channel", "here", or "this thread"' in read

    send = SlackSendMessageTool.description
    assert "run any lookup first and send its actual result" in send
    assert "prefer slack_reply_message" in send
