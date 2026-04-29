"""Tests for ReplSession state."""

from __future__ import annotations

from app.cli.repl.session import ReplSession


class TestReplSession:
    def test_defaults(self) -> None:
        session = ReplSession()
        assert session.history == []
        assert session.last_state is None
        assert session.accumulated_context == {}
        assert session.trust_mode is False

    def test_record_appends_entry(self) -> None:
        session = ReplSession()
        session.record("alert", "cpu high")
        session.record("slash", "/status", ok=True)
        session.record("alert", "bad one", ok=False)
        assert len(session.history) == 3
        assert session.history[-1]["type"] == "alert"
        assert session.history[-1]["ok"] is False

    def test_clear_preserves_trust_mode(self) -> None:
        session = ReplSession()
        session.trust_mode = True
        session.accumulated_context["service"] = "api"
        session.record("alert", "something")
        session.last_state = {"foo": "bar"}

        session.clear()

        assert session.history == []
        assert session.last_state is None
        assert session.accumulated_context == {}
        assert session.trust_mode is True  # preserved intentionally

    def test_accumulate_from_state_extracts_known_keys(self) -> None:
        session = ReplSession()
        session.accumulate_from_state(
            {
                "service": "orders-api",
                "pipeline_name": "events_fact",
                "cluster_name": "prod-us-east",
                "region": "us-east-1",
                "environment": "production",
                "root_cause": "disk full",  # not accumulated
                "evidence": {"ev-1": "x"},  # not accumulated
            }
        )
        assert session.accumulated_context == {
            "service": "orders-api",
            "pipeline_name": "events_fact",
            "cluster_name": "prod-us-east",
            "region": "us-east-1",
            "environment": "production",
        }

    def test_accumulate_from_state_skips_empty_and_none(self) -> None:
        session = ReplSession()
        session.accumulate_from_state(
            {
                "service": "",
                "cluster_name": None,
                "region": "us-east-1",
            }
        )
        assert session.accumulated_context == {"region": "us-east-1"}

    def test_accumulate_from_state_merges_across_calls(self) -> None:
        """Subsequent investigations fill in context the earlier one didn't have."""
        session = ReplSession()
        session.accumulate_from_state({"service": "orders-api"})
        session.accumulate_from_state({"cluster_name": "prod-us-east"})
        assert session.accumulated_context == {
            "service": "orders-api",
            "cluster_name": "prod-us-east",
        }

    def test_accumulate_from_state_handles_none_and_empty_state(self) -> None:
        session = ReplSession()
        session.accumulate_from_state(None)
        session.accumulate_from_state({})
        assert session.accumulated_context == {}
