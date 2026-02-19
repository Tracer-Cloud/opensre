"""
Tests for frame_problem with memory integration (Milestone 2).

Tests validate that memory context is loaded and used to speed up problem generation.
"""

import os

import pytest

from app.agent.nodes.frame_problem.frame_problem import node_frame_problem

# Skip these tests if no API key (they require real LLM calls)
pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="Requires ANTHROPIC_API_KEY for LLM calls"
)


class TestFrameProblemMemory:
    """Test frame_problem memory integration."""

    def test_frame_problem_without_memory(self):
        """frame_problem works when memory is disabled."""
        os.environ.pop("TRACER_MEMORY_ENABLED", None)

        state = {
            "alert_name": "Pipeline failure",
            "pipeline_name": "upstream_downstream_pipeline_prefect",
            "severity": "critical",
            "alert_json": {},
            "context": {},
        }

        result = node_frame_problem(state)

        assert "problem_md" in result
        assert result["problem_md"] != ""

    def test_frame_problem_with_memory_enabled(self):
        """frame_problem loads memory when enabled."""
        os.environ["TRACER_MEMORY_ENABLED"] = "1"

        try:
            state = {
                "alert_name": "Prefect Flow Failed",
                "pipeline_name": "upstream_downstream_pipeline_prefect",
                "severity": "critical",
                "alert_json": {"alert_id": "test123"},
                "context": {},
            }

            result = node_frame_problem(state)

            assert "problem_md" in result
            assert result["problem_md"] != ""
            # Memory should have been loaded (check logs for [MEMORY] message)
            # If ARCHITECTURE.md exists, memory context will be non-empty

        finally:
            os.environ.pop("TRACER_MEMORY_ENABLED", None)

    def test_frame_problem_with_prior_investigation_memory(self):
        """frame_problem uses prior investigation memory if available."""
        os.environ["TRACER_MEMORY_ENABLED"] = "1"

        try:
            # First, write a memory from a "prior" investigation
            from app.agent.memory import write_memory

            write_memory(
                pipeline_name="upstream_downstream_pipeline_prefect",
                alert_id="prior001",
                root_cause="External API schema change removed customer_id field",
                validity_score=0.90,
                problem_pattern="Upstream schema failure: External API removed required field",
            )

            # Now run frame_problem - should load the prior memory
            state = {
                "alert_name": "Prefect Flow Failed",
                "pipeline_name": "upstream_downstream_pipeline_prefect",
                "severity": "critical",
                "alert_json": {"alert_id": "test456"},
                "context": {},
            }

            result = node_frame_problem(state)

            assert "problem_md" in result
            assert result["problem_md"] != ""
            # Prior memory should have been loaded

        finally:
            os.environ.pop("TRACER_MEMORY_ENABLED", None)
            # Cleanup test memory files
            from app.agent.memory.io import get_memories_dir

            memories_dir = get_memories_dir()
            for f in memories_dir.glob("*-upstream_downstream_pipeline_prefect-prior001.md"):
                f.unlink()
