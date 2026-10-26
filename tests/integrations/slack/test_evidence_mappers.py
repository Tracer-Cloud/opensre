"""Evidence mapper tests for the Slack read tools.

Each mapper turns a read tool's output into one citeable catalog entry. The
shared rule they all pin: an empty or failed read records *nothing*, because a
"0 items" entry is context the agent re-reads on every later turn for no claim.
"""

from typing import Any

import pytest

from core.tool_framework.utils import tool_unavailable
from integrations.slack.tools.slack_list_members_tool.tool import _map_slack_list_team_members
from integrations.slack.tools.slack_read_list_tool.tool import _map_slack_read_list
from integrations.slack.tools.slack_read_messages_tool.tool import _map_slack_read_messages
from integrations.slack.tools.slack_search_messages_tool.tool import _map_slack_search_messages
from integrations.slack.tools.slack_thread_replay_tool.tool import (
    _map_replay_slack_thread_locally,
)


class TestSlackReadMessagesMapper:
    def test_records_message_count_and_channel(self) -> None:
        # Arrange
        evidence: dict[str, Any] = {}
        output = {
            "source": "slack",
            "available": True,
            "status": "read",
            "channel_id": "C0INCIDENT",
            "messages": [
                {"user": "U1", "ts": "1.0", "text": "deploy started"},
                {"user": "U2", "ts": "2.0", "text": "errors spiking"},
            ],
            "message_count": 2,
            "truncated": False,
        }

        # Act
        _map_slack_read_messages(evidence, output, {"channel_id": "C0INCIDENT"})

        # Assert
        assert evidence["catalog_entries"] == [
            {
                "source": "slack_read_messages",
                "label": "Slack Channel Messages",
                "summary": "2 messages from C0INCIDENT",
                "url": None,
                "snippet": None,
            }
        ]

    def test_marks_a_page_capped_read_as_truncated(self) -> None:
        """A full page is a floor, not a total — citing it as exact would mislead."""
        # Arrange
        evidence: dict[str, Any] = {}
        output = {
            "status": "read",
            "channel_id": "C0INCIDENT",
            "messages": [{"text": f"m{i}"} for i in range(50)],
            "message_count": 50,
            "truncated": True,
        }

        # Act
        _map_slack_read_messages(evidence, output, {"channel_id": "C0INCIDENT", "limit": 50})

        # Assert
        assert (
            evidence["catalog_entries"][0]["summary"] == "50 messages from C0INCIDENT (truncated)"
        )

    @pytest.mark.parametrize(
        "output",
        [
            pytest.param({}, id="empty-payload"),
            pytest.param(
                {"status": "read", "channel_id": "C1", "messages": [], "message_count": 0},
                id="no-messages",
            ),
            pytest.param(
                {
                    "source": "slack",
                    "available": True,
                    "status": "failed",
                    "error": "channel_not_found",
                    "error_type": "api_error",
                    "messages": [],
                    "message_count": 0,
                },
                id="failed-read",
            ),
        ],
    )
    def test_records_nothing_without_messages(self, output: dict[str, Any]) -> None:
        evidence: dict[str, Any] = {}

        _map_slack_read_messages(evidence, output, {})

        assert evidence == {}


class TestSlackSearchMessagesMapper:
    def test_records_match_count_query_and_top_permalink(self) -> None:
        # Arrange
        evidence: dict[str, Any] = {}
        output = {
            "source": "slack",
            "available": True,
            "status": "read",
            "matches": [
                {
                    "channel_id": "C1",
                    "user": "U1",
                    "ts": "1.0",
                    "text": "timeout",
                    "permalink": "https://slack.example/p1",
                },
                {"channel_id": "C2", "user": "U2", "ts": "2.0", "text": "timeout again"},
            ],
            "match_count": 2,
            "truncated": False,
        }

        # Act
        _map_slack_search_messages(evidence, output, {"query": "in:#incidents timeout"})

        # Assert
        assert evidence["catalog_entries"] == [
            {
                "source": "slack_search_messages",
                "label": "Slack Message Search",
                "summary": "2 matches for 'in:#incidents timeout'",
                "url": "https://slack.example/p1",
                "snippet": None,
            }
        ]

    def test_marks_a_count_capped_search_as_truncated(self) -> None:
        """search.messages returns one page, so a full page may hide more hits."""
        # Arrange
        evidence: dict[str, Any] = {}
        output = {
            "status": "read",
            "matches": [{"text": f"hit {i}"} for i in range(20)],
            "match_count": 20,
            "truncated": True,
        }

        # Act
        _map_slack_search_messages(evidence, output, {"query": "timeout"})

        # Assert
        assert evidence["catalog_entries"][0]["summary"] == "20 matches for 'timeout' (truncated)"

    def test_caps_the_query_echoed_into_the_summary(self) -> None:
        """The entry is re-read every turn, so a pathological query cannot grow it."""
        # Arrange
        evidence: dict[str, Any] = {}
        query = "timeout " * 50

        # Act
        _map_slack_search_messages(evidence, {"matches": [{"text": "x"}]}, {"query": query})

        # Assert
        summary = evidence["catalog_entries"][0]["summary"]
        assert len(summary) < 120
        assert summary.startswith("1 matches for 'timeout timeout")

    @pytest.mark.parametrize(
        "output",
        [
            pytest.param({}, id="empty-payload"),
            pytest.param(
                tool_unavailable("slack", "no user token", matches=[], match_count=0),
                id="unavailable-envelope",
            ),
            pytest.param({"status": "read", "matches": [], "match_count": 0}, id="no-matches"),
        ],
    )
    def test_records_nothing_without_matches(self, output: dict[str, Any]) -> None:
        evidence: dict[str, Any] = {}

        _map_slack_search_messages(evidence, output, {"query": "timeout"})

        assert evidence == {}


class TestSlackListTeamMembersMapper:
    def test_records_member_count(self) -> None:
        # Arrange
        evidence: dict[str, Any] = {}
        output = {
            "status": "read",
            "members": [
                {"id": "U1", "username": "ada", "is_bot": False},
                {"id": "U2", "username": "grace", "is_bot": False},
            ],
            "member_count": 2,
            "truncated": False,
        }

        # Act
        _map_slack_list_team_members(evidence, output, {})

        # Assert
        assert evidence["catalog_entries"] == [
            {
                "source": "slack_list_team_members",
                "label": "Slack Workspace Roster",
                "summary": "2 members",
                "url": None,
                "snippet": None,
            }
        ]

    def test_marks_a_page_capped_roster_as_truncated(self) -> None:
        """A partial roster must not read as the whole team."""
        # Arrange
        evidence: dict[str, Any] = {}

        # Act
        _map_slack_list_team_members(evidence, {"members": [{"id": "U1"}], "truncated": True}, {})

        # Assert
        assert evidence["catalog_entries"][0]["summary"] == "1 members (truncated)"

    @pytest.mark.parametrize(
        "output",
        [
            pytest.param({}, id="empty-payload"),
            pytest.param(
                tool_unavailable("slack", "bot token missing", members=[], member_count=0),
                id="unavailable-envelope",
            ),
            pytest.param({"status": "read", "members": []}, id="no-members"),
        ],
    )
    def test_records_nothing_without_members(self, output: dict[str, Any]) -> None:
        evidence: dict[str, Any] = {}

        _map_slack_list_team_members(evidence, output, {})

        assert evidence == {}


class TestSlackReadListMapper:
    def test_records_row_count_and_list_title(self) -> None:
        # Arrange
        evidence: dict[str, Any] = {}
        output = {
            "status": "read",
            "list_id": "F0123ABCD",
            "list_title": "OpenSRE Team Tasks",
            "lists": [],
            "items": [{"id": "1", "name": "Rotate keys"}, {"id": "2", "name": "Patch runner"}],
            "item_count": 2,
            "truncated": False,
        }

        # Act
        _map_slack_read_list(evidence, output, {})

        # Assert
        assert evidence["catalog_entries"] == [
            {
                "source": "slack_read_list",
                "label": "Slack List",
                "summary": "2 rows in OpenSRE Team Tasks",
                "url": None,
                "snippet": None,
            }
        ]

    def test_falls_back_to_the_list_id_when_discovery_found_no_title(self) -> None:
        # Arrange
        evidence: dict[str, Any] = {}

        # Act
        _map_slack_read_list(
            evidence,
            {"list_id": "F0123ABCD", "list_title": "", "items": [{"id": "1"}], "truncated": True},
            {},
        )

        # Assert
        assert evidence["catalog_entries"][0]["summary"] == "1 rows in F0123ABCD (truncated)"

    @pytest.mark.parametrize(
        "output",
        [
            pytest.param({}, id="empty-payload"),
            pytest.param(
                {"status": "read", "list_id": "", "lists": [{"list_id": "F1"}], "items": []},
                id="ambiguous-discovery-only",
            ),
            pytest.param(
                {
                    "status": "failed",
                    "error": "lists:read missing",
                    "error_type": "api_error",
                    "items": [],
                },
                id="failed-read",
            ),
        ],
    )
    def test_records_nothing_without_rows(self, output: dict[str, Any]) -> None:
        evidence: dict[str, Any] = {}

        _map_slack_read_list(evidence, output, {})

        assert evidence == {}


class TestReplaySlackThreadLocallyMapper:
    def test_records_thread_message_count_and_reference(self) -> None:
        # Arrange
        evidence: dict[str, Any] = {}
        output = {
            "status": "ok",
            "thread": {
                "channel": "C0INCIDENT",
                "ts": "1700000000.000100",
                "messages": [
                    {"user": "U1", "text": "paging on-call", "ts": "1.0", "reactions": []},
                    {"user": "U2", "text": "rolling back", "ts": "2.0", "reactions": ["eyes"]},
                ],
                "count": 2,
                "truncated": False,
            },
        }

        # Act
        _map_replay_slack_thread_locally(
            evidence, output, {"thread_ref": "C0INCIDENT/1700000000.000100"}
        )

        # Assert
        assert evidence["catalog_entries"] == [
            {
                "source": "replay_slack_thread_locally",
                "label": "Slack Thread Replay",
                "summary": "2 thread messages from C0INCIDENT/1700000000.000100",
                "url": None,
                "snippet": None,
            }
        ]

    @pytest.mark.parametrize(
        "output",
        [
            pytest.param({}, id="empty-payload"),
            pytest.param(
                {"status": "failed", "error": "thread_not_found", "error_type": "delivery_error"},
                id="failed-fetch",
            ),
            pytest.param(
                {"status": "ok", "thread": {"channel": "C1", "ts": "1.0", "messages": []}},
                id="empty-thread",
            ),
        ],
    )
    def test_records_nothing_without_thread_messages(self, output: dict[str, Any]) -> None:
        evidence: dict[str, Any] = {}

        _map_replay_slack_thread_locally(evidence, output, {})

        assert evidence == {}
