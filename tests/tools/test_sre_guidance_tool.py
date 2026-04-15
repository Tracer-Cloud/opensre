"""Tests for SREGuidanceTool (function-based, @tool decorated)."""

from __future__ import annotations

from app.tools.SREGuidanceTool import get_sre_guidance
from tests.tools.conftest import BaseToolContract


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
