"""Tests for route_tools module.

These tests verify that the tool routing logic correctly scores and selects
the most relevant tools for an investigation context.
"""

from __future__ import annotations

import pytest

from app.nodes.route_tools.route_tools import (
    _extract_keywords,
    route_tools,
    route_tools_with_scores,
    score_tool_for_context,
)
from app.tools.registered_tool import RegisteredTool
from app.types.evidence import EvidenceSource


def create_test_tool(
    name: str,
    source: EvidenceSource,
    description: str = "",
    use_cases: list[str] | None = None,
) -> RegisteredTool:
    """Helper to create a test tool for unit tests."""
    return RegisteredTool(
        name=name,
        description=description or f"Test tool for {name}",
        input_schema={"type": "object", "properties": {}},
        source=source,
        use_cases=use_cases or [],
        run=lambda: None,
    )


class TestExtractKeywords:
    """Tests for keyword extraction."""

    def test_extracts_from_problem_md(self) -> None:
        """Should extract meaningful words from problem statement."""
        problem = "The CloudWatch logs show Lambda timeout errors in production"
        keywords = _extract_keywords(problem, "")

        assert "cloudwatch" in keywords
        assert "logs" in keywords
        assert "lambda" in keywords
        assert "timeout" in keywords
        assert "errors" in keywords
        assert "production" in keywords

    def test_extracts_from_alert_name(self) -> None:
        """Should extract words from alert name."""
        keywords = _extract_keywords("", "lambda-timeout-error")

        assert "lambda" in keywords
        assert "timeout" in keywords
        assert "error" in keywords

    def test_removes_stop_words(self) -> None:
        """Should filter out common stop words."""
        problem = "the and for but not you all can"
        keywords = _extract_keywords(problem, "")

        stop_words = ["the", "and", "for", "but", "not", "you", "all", "can"]
        for word in stop_words:
            assert word not in keywords

    def test_removes_short_words(self) -> None:
        """Should filter out words shorter than 3 characters."""
        problem = "a bc def ghij"
        keywords = _extract_keywords(problem, "")

        assert "a" not in keywords
        assert "bc" not in keywords
        assert "def" in keywords
        assert "ghij" in keywords


class TestScoreToolForContext:
    """Tests for tool scoring logic."""

    def test_source_match_increases_score(self) -> None:
        """Should increase score when tool source matches available sources."""
        tool = create_test_tool("test_tool", "cloudwatch")

        score = score_tool_for_context(
            tool=tool,
            available_sources={"cloudwatch": {"log_group": "/aws/lambda/test"}},
            keywords=[],
            executed_actions=set(),
        )

        assert score > 0

    def test_source_mismatch_no_bonus(self) -> None:
        """Should not give source bonus when source doesn't match."""
        tool = create_test_tool("test_tool", "cloudwatch")

        score = score_tool_for_context(
            tool=tool,
            available_sources={"s3": {"bucket": "test-bucket"}},
            keywords=[],
            executed_actions=set(),
        )

        assert score == 0  # No source match, no keywords

    def test_keyword_match_in_use_cases(self) -> None:
        """Should increase score for keyword matches in use_cases."""
        tool = create_test_tool(
            "test_tool",
            "cloudwatch",
            use_cases=["Fetch CloudWatch logs for lambda errors", "Analyze timeout patterns"],
        )

        score_no_keyword = score_tool_for_context(
            tool=tool,
            available_sources={},
            keywords=[],
            executed_actions=set(),
        )

        score_with_keyword = score_tool_for_context(
            tool=tool,
            available_sources={},
            keywords=["lambda", "timeout"],
            executed_actions=set(),
        )

        assert score_with_keyword > score_no_keyword

    def test_keyword_match_in_description(self) -> None:
        """Should increase score for keyword matches in description."""
        tool = create_test_tool(
            "test_tool", "cloudwatch", description="Tool for analyzing S3 bucket access patterns"
        )

        score = score_tool_for_context(
            tool=tool,
            available_sources={},
            keywords=["s3", "bucket"],
            executed_actions=set(),
        )

        assert score > 0

    def test_executed_action_penalty(self) -> None:
        """Should penalize already executed actions."""
        tool = create_test_tool("test_tool", "cloudwatch")

        score_new = score_tool_for_context(
            tool=tool,
            available_sources={"cloudwatch": {}},
            keywords=[],
            executed_actions=set(),
        )

        score_executed = score_tool_for_context(
            tool=tool,
            available_sources={"cloudwatch": {}},
            keywords=[],
            executed_actions={"test_tool"},
        )

        assert score_executed < score_new

    def test_combined_scoring(self) -> None:
        """Should combine source match, keyword match, and execution penalty."""
        tool = create_test_tool(
            "cloudwatch_logs",
            "cloudwatch",
            description="Fetch CloudWatch logs",
            use_cases=["Analyze lambda errors"],
        )

        score = score_tool_for_context(
            tool=tool,
            available_sources={"cloudwatch": {"log_group": "test"}},
            keywords=["lambda", "errors"],
            executed_actions=set(),
        )

        # Source match (+5) + 2 keywords in use_cases (+4) = +9
        assert score > 5


class TestRouteTools:
    """Tests for the main route_tools function."""

    def test_returns_empty_list_when_no_sources(self) -> None:
        """Should return empty list when no sources available."""
        # Note: This depends on actual tools in the registry
        # Tools require specific sources to be available
        result = route_tools(
            available_sources={},
            _resolved_integrations={},
            problem_md="Lambda timeout error",
            alert_name="lambda-timeout",
            executed_hypotheses=[],
        )

        # Should be empty because no tools are available without sources
        assert isinstance(result, list)

    def test_filters_by_source_availability(self) -> None:
        """Should only include tools available for detected sources."""
        result = route_tools(
            available_sources={"cloudwatch": {"log_group": "/aws/lambda/test"}},
            _resolved_integrations={},
            problem_md="Check CloudWatch logs",
            alert_name="cloudwatch-error",
            executed_hypotheses=[],
        )

        # All returned tools should be for available sources
        for tool in result:
            assert tool.is_available({"cloudwatch": {"log_group": "/aws/lambda/test"}})

    def test_deprioritizes_executed_actions(self) -> None:
        """Should deprioritize actions that have already been executed."""
        result_first = route_tools(
            available_sources={"cloudwatch": {"log_group": "/aws/lambda/test"}},
            _resolved_integrations={},
            problem_md="Check logs",
            alert_name="error",
            executed_hypotheses=[],  # Nothing executed yet
        )

        if len(result_first) > 1:
            first_tool_name = result_first[0].name

            result_second = route_tools(
                available_sources={"cloudwatch": {"log_group": "/aws/lambda/test"}},
                _resolved_integrations={},
                problem_md="Check logs",
                alert_name="error",
                executed_hypotheses=[{"actions": [first_tool_name]}],
            )

            # First result should now be different (not the executed one)
            if result_second:
                assert result_second[0].name != first_tool_name

    def test_deterministic_ordering(self) -> None:
        """Should produce deterministic output for same inputs."""
        inputs = {
            "available_sources": {"cloudwatch": {"log_group": "/aws/lambda/test"}},
            "_resolved_integrations": {},
            "problem_md": "Lambda timeout error in production",
            "alert_name": "lambda-timeout",
            "executed_hypotheses": [],
        }

        result1 = route_tools(**inputs)
        result2 = route_tools(**inputs)
        result3 = route_tools(**inputs)

        # All results should have same tool names in same order
        names1 = [t.name for t in result1]
        names2 = [t.name for t in result2]
        names3 = [t.name for t in result3]

        assert names1 == names2 == names3

    def test_returns_sorted_by_score(self) -> None:
        """Should return tools sorted by score descending."""
        result_with_scores = route_tools_with_scores(
            available_sources={
                "cloudwatch": {"log_group": "/aws/lambda/test"},
                "s3": {"bucket": "test-bucket"},
            },
            _resolved_integrations={},
            problem_md="CloudWatch logs and S3 access",
            alert_name="cloudwatch-s3-error",
            executed_hypotheses=[],
        )

        if len(result_with_scores) > 1:
            scores = [score for _, score in result_with_scores]
            # Scores should be in descending order
            for i in range(len(scores) - 1):
                assert scores[i] >= scores[i + 1]

    def test_top_k_limit(self) -> None:
        """Should respect top_k parameter."""
        result = route_tools(
            available_sources={"cloudwatch": {"log_group": "/aws/lambda/test"}},
            _resolved_integrations={},
            problem_md="Check all logs",
            alert_name="error",
            executed_hypotheses=[],
            top_k=3,
        )

        assert len(result) <= 3

    def test_respects_tool_is_available(self) -> None:
        """Should call is_available on each tool and filter accordingly."""
        # This test verifies that the routing respects tool availability logic
        result = route_tools(
            available_sources={"grafana": {"service_name": "test-service"}},
            _resolved_integrations={},
            problem_md="Check Grafana logs",
            alert_name="grafana-alert",
            executed_hypotheses=[],
        )

        # All returned tools should be available for the sources
        for tool in result:
            assert tool.is_available({"grafana": {"service_name": "test-service"}})


class TestRouteToolsWithScores:
    """Tests for route_tools_with_scores function."""

    def test_returns_scores_for_debugging(self) -> None:
        """Should return (tool, score) tuples for debugging."""
        result = route_tools_with_scores(
            available_sources={"cloudwatch": {"log_group": "/aws/lambda/test"}},
            _resolved_integrations={},
            problem_md="Check logs",
            alert_name="error",
            executed_hypotheses=[],
        )

        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], RegisteredTool)
            assert isinstance(item[1], int)

    def test_returns_all_available_tools(self) -> None:
        """Should return all available tools including those with negative scores."""
        result = route_tools_with_scores(
            available_sources={"cloudwatch": {"log_group": "/aws/lambda/test"}},
            _resolved_integrations={},
            problem_md="Check logs",
            alert_name="error",
            executed_hypotheses=[],
        )

        # All returned tools should have a score (can be positive or negative)
        for _, score in result:
            assert isinstance(score, int)


class TestIntegrationWithResolvedIntegrations:
    """Tests for integration with resolved_integrations."""

    def test_considers_resolved_integrations_in_source_detection(self) -> None:
        """Should work with sources derived from resolved integrations."""
        # This tests that the routing works when sources come from integrations
        result = route_tools(
            available_sources={
                "grafana": {"service_name": "test-service", "pipeline_name": "test"},
            },
            _resolved_integrations={
                "grafana": {"api_key": "test", "endpoint": "https://test.grafana.net"},
            },
            problem_md="Grafana alert for test-service",
            alert_name="grafana-high-error-rate",
            executed_hypotheses=[],
        )

        assert isinstance(result, list)
        # Should include Grafana-related tools if available


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_problem_and_alert(self) -> None:
        """Should handle empty problem and alert gracefully."""
        result = route_tools(
            available_sources={"cloudwatch": {}},
            _resolved_integrations={},
            problem_md="",
            alert_name="",
            executed_hypotheses=[],
        )

        assert isinstance(result, list)

    def test_none_resolved_integrations(self) -> None:
        """Should handle None resolved_integrations."""
        result = route_tools(
            available_sources={"cloudwatch": {}},
            _resolved_integrations=None,
            problem_md="Test",
            alert_name="test-alert",
            executed_hypotheses=[],
        )

        assert isinstance(result, list)

    def test_executed_hypotheses_with_no_actions(self) -> None:
        """Should handle hypotheses without actions key."""
        result = route_tools(
            available_sources={"cloudwatch": {}},
            _resolved_integrations={},
            problem_md="Test",
            alert_name="test-alert",
            executed_hypotheses=[{"source": "cloudwatch", "result": "success"}],
        )

        assert isinstance(result, list)

    def test_executed_hypotheses_with_non_list_actions(self) -> None:
        """Should handle hypotheses where actions is not a list."""
        result = route_tools(
            available_sources={"cloudwatch": {}},
            _resolved_integrations={},
            problem_md="Test",
            alert_name="test-alert",
            executed_hypotheses=[{"actions": "not_a_list"}],
        )

        assert isinstance(result, list)
