"""Tests for follow-up summarization and evidence handling."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

from rich.console import Console

from app.cli.repl.follow_up import (
    _summarize_evidence,
    _summarize_last_state,
    answer_follow_up,
)
from app.cli.repl.session import ReplSession


class TestSummarizeEvidence:
    def test_dict_evidence_includes_keys_and_sample(self) -> None:
        evidence = {
            "ev-1": {"kind": "log", "text": "disk full"},
            "ev-2": {"kind": "metric", "value": 99.8},
            "ev-3": {"kind": "trace", "latency_ms": 4200},
            "ev-4": {"kind": "event", "message": "restarted pod"},
        }
        parts = _summarize_evidence(evidence)
        joined = "\n".join(parts)
        assert "Evidence items: 4" in joined
        assert "ev-1" in joined
        assert "ev-2" in joined
        assert "ev-3" in joined
        # Only the first 3 keys are sampled into the JSON body.
        assert "ev-4" not in joined

    def test_list_evidence_slices_first_three(self) -> None:
        evidence = [{"id": i, "kind": "log", "text": f"line {i}"} for i in range(10)]
        parts = _summarize_evidence(evidence)
        joined = "\n".join(parts)
        assert "Evidence items: 10" in joined
        assert '"id": 0' in joined
        assert '"id": 2' in joined
        assert '"id": 3' not in joined

    def test_other_type_falls_back_to_string(self) -> None:
        parts = _summarize_evidence("a raw evidence blob")
        joined = "\n".join(parts)
        assert "Evidence type: str" in joined
        assert "a raw evidence blob" in joined


class TestSummarizeLastState:
    def test_dict_evidence_is_grounded_not_dropped(self) -> None:
        # Regression: evidence used to be indexed as a list (evidence[:3]),
        # which raised TypeError for the real dict shape and silently dropped
        # all evidence context from follow-up prompts.
        state = {
            "alert_name": "Orders API 5xx spike",
            "root_cause": "Redis cache eviction storm",
            "evidence": {
                "ev-1": {"kind": "log", "text": "OOMKilled"},
                "ev-2": {"kind": "metric", "name": "cache_hit_rate", "value": 0.12},
            },
        }
        summary = _summarize_last_state(state)
        assert "Evidence items: 2" in summary
        assert "ev-1" in summary
        assert "OOMKilled" in summary
        assert "(evidence present but could not be serialized" not in summary

    def test_empty_state_uses_placeholder(self) -> None:
        assert _summarize_last_state({}) == "(no prior investigation details available)"

    def test_missing_evidence_is_silent(self) -> None:
        state = {"alert_name": "Test", "root_cause": "unknown"}
        summary = _summarize_last_state(state)
        assert "Alert: Test" in summary
        assert "Evidence" not in summary


class TestAnswerFollowUpMarkupSafety:
    """Regression: LLM output with bracket sequences (e.g. [OOMKilled]) was
    being silently truncated by Rich's markup parser.  All LLM and exception
    text must be markup-escaped before interpolation."""

    def _run_with_response(self, monkeypatch: object, response_text: str) -> str:
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.content = response_text
        fake_client.invoke.return_value = fake_response

        # follow_up.py imports get_llm_for_reasoning lazily inside the function,
        # so patch at the source module.
        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            lambda: fake_client,
        )

        session = ReplSession()
        session.last_state = {"alert_name": "test", "root_cause": "x"}

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, highlight=False, width=200)
        answer_follow_up("what happened?", session, console)
        return buf.getvalue()

    def test_llm_output_with_brackets_not_truncated(self, monkeypatch: object) -> None:
        response = (
            "The pod hit [OOMKilled] at [2026-04-15 14:00:00 UTC] on "
            "[service-name=orders-api] — see [ERROR] in logs."
        )
        output = self._run_with_response(monkeypatch, response)
        assert "[OOMKilled]" in output
        assert "[ERROR]" in output
        assert "orders-api" in output
        assert "2026-04-15 14:00:00 UTC" in output

    def test_exception_message_with_brackets_not_dropped(self, monkeypatch: object) -> None:
        def _boom() -> None:
            raise RuntimeError("config error: missing [api_key] in [datadog] section")

        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            _boom,
        )

        session = ReplSession()
        session.last_state = {"alert_name": "test", "root_cause": "x"}

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, highlight=False, width=200)
        answer_follow_up("why?", session, console)
        output = buf.getvalue()
        assert "[api_key]" in output
        assert "[datadog]" in output
