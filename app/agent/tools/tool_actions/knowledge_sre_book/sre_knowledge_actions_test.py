"""Integration tests for SRE knowledge retrieval action."""

from app.agent.tools.tool_actions.knowledge_sre_book.sre_knowledge_base import SRE_TOPICS, get_topics_for_keywords
from app.agent.tools.tool_actions.knowledge_sre_book.sre_knowledge_actions import get_sre_guidance


class TestGetSREGuidance:
    """Tests for the get_sre_guidance tool action."""

    def test_get_specific_topic(self):
        """Should return content for a specific topic."""
        result = get_sre_guidance(topic="failure_delayed_data")

        assert result["success"] is True
        assert result["topics"] == ["failure_delayed_data"]
        assert len(result["guidance"]) == 1
        assert "Delayed Data" in result["guidance"][0]["topic"]
        assert "hanging" in result["guidance"][0]["content"].lower()
        assert result["sources"][0].startswith("SRE")

    def test_get_topic_by_keywords(self):
        """Should match topics by keywords."""
        result = get_sre_guidance(keywords=["timeout", "delay", "stuck"])

        assert result["success"] is True
        assert "failure_delayed_data" in result["topics"]
        assert len(result["guidance"]) > 0

    def test_keywords_match_hotspotting(self):
        """Should find hotspotting topic for resource keywords."""
        result = get_sre_guidance(keywords=["cpu", "memory", "bottleneck"])

        assert result["success"] is True
        assert "hotspotting" in result["topics"]

    def test_keywords_match_freshness_slo(self):
        """Should find SLO freshness topic for latency keywords."""
        result = get_sre_guidance(keywords=["freshness", "stale", "latency"])

        assert result["success"] is True
        assert "slo_freshness" in result["topics"]

    def test_max_topics_limit(self):
        """Should respect max_topics parameter."""
        result = get_sre_guidance(keywords=["data", "pipeline"], max_topics=2)

        assert len(result["topics"]) <= 2

    def test_no_matching_keywords(self):
        """Should return success=False when no keywords match."""
        result = get_sre_guidance(keywords=["xyz123nonexistent"])

        assert result["success"] is False
        assert "message" in result

    def test_empty_keywords(self):
        """Should return empty results for empty keywords."""
        result = get_sre_guidance(keywords=[])

        assert result["success"] is False

    def test_invalid_topic(self):
        """Should return empty for invalid topic name."""
        result = get_sre_guidance(topic="nonexistent_topic")

        assert result["topics"] == []
        assert result["guidance"] == []


class TestGetTopicsForKeywords:
    """Tests for keyword matching logic."""

    def test_partial_keyword_match(self):
        """Should match partial keywords."""
        topics = get_topics_for_keywords(["fresh"])
        assert "slo_freshness" in topics

    def test_multiple_keyword_scoring(self):
        """Should rank by number of keyword matches."""
        topics = get_topics_for_keywords(["delay", "timeout", "hung"])
        # failure_delayed_data should rank high (matches all three)
        assert topics[0] == "failure_delayed_data"

    def test_case_insensitive(self):
        """Should match keywords case-insensitively."""
        topics = get_topics_for_keywords(["ETL", "BATCH"])
        assert "pipeline_types" in topics


class TestSRETopicsContent:
    """Tests to verify SRE content quality."""

    def test_all_topics_have_required_fields(self):
        """All topics should have name, keywords, content, source."""
        for topic_name, topic in SRE_TOPICS.items():
            assert topic.name, f"{topic_name} missing name"
            assert topic.keywords, f"{topic_name} missing keywords"
            assert topic.content, f"{topic_name} missing content"
            assert topic.source, f"{topic_name} missing source"

    def test_sources_reference_sre_books(self):
        """All sources should reference SRE books/workbook."""
        for topic_name, topic in SRE_TOPICS.items():
            assert "SRE" in topic.source, f"{topic_name} source should reference SRE"

    def test_minimum_topic_count(self):
        """Should have a reasonable number of topics."""
        assert len(SRE_TOPICS) >= 10, "Expected at least 10 SRE topics"


class TestActionIntegration:
    """Tests for integration with investigation actions registry."""

    def test_action_registered(self):
        """get_sre_guidance should be registered in available actions."""
        from app.agent.tools.tool_actions.investigation_registry import (
            get_available_actions,
        )

        actions = get_available_actions()
        action_names = [a.name for a in actions]

        assert "get_sre_guidance" in action_names

    def test_action_always_available(self):
        """SRE guidance should always be available (no external deps)."""
        from app.agent.tools.tool_actions.investigation_registry import (
            get_available_actions,
        )

        actions = get_available_actions()
        sre_action = next(a for a in actions if a.name == "get_sre_guidance")

        # Should be available with empty sources
        assert sre_action.availability_check({}) is True

    def test_action_source_is_knowledge(self):
        """SRE guidance action should have 'knowledge' source type."""
        from app.agent.tools.tool_actions.investigation_registry import (
            get_available_actions,
        )

        actions = get_available_actions()
        sre_action = next(a for a in actions if a.name == "get_sre_guidance")

        assert sre_action.source == "knowledge"
