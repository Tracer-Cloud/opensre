"""Tests for SREGuidanceTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any

from tests.tools.conftest import BaseToolContract
from tools.system.sre_guidance_tool import get_sre_guidance
from tools.system.sre_guidance_tool._evidence import map_get_sre_guidance


class TestSREGuidanceToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_sre_guidance.__opensre_registered_tool__


def test_run_with_topic_returns_guidance() -> None:
    result = get_sre_guidance(topic="pipeline_types")
    assert isinstance(result, dict)


def test_run_with_keywords_returns_guidance() -> None:
    result = get_sre_guidance(keywords=["timeout", "delay"])
    assert isinstance(result, dict)


def test_run_no_args_returns_something() -> None:
    result = get_sre_guidance()
    assert isinstance(result, dict)


def test_run_unknown_topic_doesnt_crash() -> None:
    result = get_sre_guidance(topic="nonexistent_topic_xyz")
    assert isinstance(result, dict)


def test_metadata_has_knowledge_source() -> None:
    rt = get_sre_guidance.__opensre_registered_tool__
    assert rt.source == "knowledge"
    assert rt.name == "get_sre_guidance"


def test_map_get_sre_guidance_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_sre_guidance(evidence, {"topics": ["failure_delayed_data", "slo_freshness"]}, {})
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_sre_guidance"
    assert entries[0]["summary"] == "2 topic(s): failure_delayed_data, slo_freshness"


def test_map_get_sre_guidance_skips_empty_topics() -> None:
    evidence: dict[str, Any] = {}
    map_get_sre_guidance(evidence, {"topics": []}, {})
    assert "catalog_entries" not in evidence


def test_map_get_sre_guidance_disambiguates_repeat_calls() -> None:
    evidence: dict[str, Any] = {}
    map_get_sre_guidance(evidence, {"topics": ["slo_freshness"]}, {})
    map_get_sre_guidance(evidence, {"topics": ["hotspotting"]}, {})
    sources = [e["source"] for e in evidence["catalog_entries"]]
    assert sources == ["get_sre_guidance", "get_sre_guidance#2"]
