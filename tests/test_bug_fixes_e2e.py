"""End-to-end tests for PR #520 bug fixes.

Tests the full integration between components rather than individual units:
1. Guardrails: overlapping keyword redaction through the full engine pipeline
2. Investigation loop: diagnosis node + routing work together at loop counts 0-4
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from app.guardrails.engine import GuardrailEngine
from app.guardrails.rules import GuardrailAction, GuardrailRule
from app.investigation_constants import MAX_INVESTIGATION_LOOPS
from app.pipeline.routing import should_continue_investigation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    name: str = "test",
    action: str = "redact",
    patterns: list[str] | None = None,
    keywords: list[str] | None = None,
    replacement: str = "",
) -> GuardrailRule:
    compiled = tuple(re.compile(p, re.IGNORECASE) for p in (patterns or []))
    kws = tuple(k.lower() for k in (keywords or []))
    return GuardrailRule(
        name=name,
        action=GuardrailAction(action),
        patterns=compiled,
        keywords=kws,
        replacement=replacement,
    )


# ---------------------------------------------------------------------------
# 1. Guardrails overlapping keyword redaction — full pipeline
# ---------------------------------------------------------------------------


class TestOverlappingRedactionE2E:
    """Prove that overlapping keywords are fully redacted through the complete
    scan → audit → redact pipeline, not just in isolation."""

    def test_secret_and_secret_key_across_rules(self) -> None:
        """Two rules, shorter keyword is a prefix of the longer one."""
        engine = GuardrailEngine(
            [
                _make_rule(name="generic", keywords=["secret"]),
                _make_rule(name="specific", keywords=["secret_key"]),
            ]
        )
        result = engine.apply("export secret_key=hunter2")
        # The longer match must win — no leftover "_key"
        assert "_key" not in result
        assert "=hunter2" in result
        assert "secret" not in result.lower().replace("[redacted:", "")

    def test_api_and_api_key_single_rule(self) -> None:
        """Both keywords in the same rule."""
        engine = GuardrailEngine(
            [
                _make_rule(name="creds", keywords=["api", "api_key"]),
            ]
        )
        result = engine.apply("set api_key=abc123")
        assert "_key" not in result
        assert "=abc123" in result

    def test_multiple_overlapping_occurrences(self) -> None:
        """Multiple overlapping pairs in one string."""
        engine = GuardrailEngine(
            [
                _make_rule(name="r1", keywords=["pass"]),
                _make_rule(name="r2", keywords=["password"]),
            ]
        )
        text = "password=foo and pass=bar"
        result = engine.apply(text)
        # "password" should be fully redacted (not "word" leftover)
        assert "word" not in result.split("and")[0]
        # "pass" alone should also be redacted
        assert "=bar" in result

    def test_pattern_and_keyword_overlap(self) -> None:
        """A regex pattern and a keyword overlap on the same span."""
        engine = GuardrailEngine(
            [
                _make_rule(name="pat", patterns=[r"AKIA[A-Z0-9]{16}"]),
                _make_rule(name="kw", keywords=["akia"]),
            ]
        )
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = engine.apply(text)
        # The longer regex match should win
        assert "AKIA" not in result
        assert "key=" in result

    def test_full_scan_apply_audit_cycle(self) -> None:
        """Scan, check matches, then apply — the real engine flow."""
        engine = GuardrailEngine(
            [
                _make_rule(name="tokens", keywords=["token", "token_secret"]),
            ]
        )
        text = "auth token_secret=xyz and token=abc"

        # Apply correctly redacts overlapping matches without leftovers.
        result = engine.apply(text)
        assert "_secret" not in result
        assert "=xyz" in result
        assert "=abc" in result


# ---------------------------------------------------------------------------
# 2. Investigation loop — diagnosis node + routing integration
# ---------------------------------------------------------------------------


class TestInvestigationLoopE2E:
    """Prove that the diagnosis node generates recommendations at all valid
    loop counts and that the routing function continues investigation."""

    def test_constant_is_shared_correctly(self) -> None:
        """routing.py and the diagnosis node use the same constant."""
        from app.investigation_constants import MAX_INVESTIGATION_LOOPS as from_constants

        assert from_constants == 4
        assert MAX_INVESTIGATION_LOOPS == 4

    def test_routing_continues_when_recommendations_exist(self) -> None:
        """should_continue_investigation returns 'investigate' for all valid loops."""
        for loop in range(MAX_INVESTIGATION_LOOPS + 1):  # 0..4
            state = {
                "investigation_recommendations": ["do something"],
                "investigation_loop_count": loop,
                "available_action_names": ["some_action"],
            }
            result = should_continue_investigation(state)
            assert result == "investigate", f"Loop {loop} should continue but got '{result}'"

    def test_routing_publishes_when_over_limit(self) -> None:
        """Loop count > MAX_INVESTIGATION_LOOPS triggers publish."""
        state = {
            "investigation_recommendations": ["do something"],
            "investigation_loop_count": MAX_INVESTIGATION_LOOPS + 1,
            "available_action_names": ["some_action"],
        }
        assert should_continue_investigation(state) == "publish"

    def test_diagnosis_node_generates_recommendations_at_loop_3(self) -> None:
        """Before the fix, loop_count=3 would NOT generate recommendations
        because of the hardcoded `< 3` check. Now it should."""
        from app.nodes.root_cause_diagnosis.node import _handle_insufficient_evidence

        tracker = MagicMock()
        tracker.complete = MagicMock()

        state = {
            "investigation_loop_count": 3,
            "evidence": {
                "grafana_service_names": ["my-service"],
                # No grafana_logs yet — should trigger recommendation
            },
            "alert_name": "Test",
            "pipeline_name": "test-pipe",
            "severity": "critical",
        }

        result = _handle_insufficient_evidence(state, tracker)
        assert len(result["investigation_recommendations"]) > 0, (
            "At loop_count=3, recommendations should still be generated "
            "(was broken by hardcoded '< 3')"
        )

    def test_diagnosis_node_no_recommendations_at_max(self) -> None:
        """At loop_count == MAX_INVESTIGATION_LOOPS, no more recommendations."""
        from app.nodes.root_cause_diagnosis.node import _handle_insufficient_evidence

        tracker = MagicMock()
        tracker.complete = MagicMock()

        state = {
            "investigation_loop_count": MAX_INVESTIGATION_LOOPS,
            "evidence": {
                "grafana_service_names": ["my-service"],
            },
            "alert_name": "Test",
            "pipeline_name": "test-pipe",
            "severity": "critical",
        }

        result = _handle_insufficient_evidence(state, tracker)
        assert len(result["investigation_recommendations"]) == 0, (
            "At loop_count == MAX_INVESTIGATION_LOOPS, no recommendations should be generated"
        )

    def test_full_loop_lifecycle(self) -> None:
        """Simulate the full loop lifecycle: diagnosis generates recommendations
        at loops 0-3, routing continues investigation, then at loop 4 routing
        publishes."""
        from app.nodes.root_cause_diagnosis.node import _handle_insufficient_evidence

        tracker = MagicMock()
        tracker.complete = MagicMock()

        loop_count = 0
        for i in range(MAX_INVESTIGATION_LOOPS + 2):
            state = {
                "investigation_loop_count": loop_count,
                "evidence": {
                    "grafana_service_names": ["my-service"],
                },
                "alert_name": "Test",
                "pipeline_name": "test-pipe",
                "severity": "critical",
            }

            diag_result = _handle_insufficient_evidence(state, tracker)
            recs = diag_result["investigation_recommendations"]
            loop_count = diag_result["investigation_loop_count"]

            routing_state = {
                "investigation_recommendations": recs,
                "investigation_loop_count": loop_count,
                "available_action_names": ["some_action"],
            }
            route = should_continue_investigation(routing_state)

            if i < MAX_INVESTIGATION_LOOPS:
                # Loops 0-3: should generate recommendations and continue
                assert len(recs) > 0, f"Loop {i}: expected recommendations"
                assert route == "investigate", f"Loop {i}: expected 'investigate' got '{route}'"
            else:
                # Loop 4+: no recommendations, should publish
                assert len(recs) == 0, f"Loop {i}: expected no recommendations"
                assert route == "publish", f"Loop {i}: expected 'publish' got '{route}'"
                break
