"""
Tests for memory system (Milestone 1: Memory Infrastructure).

Integration tests - validates read/write and seeding from MD files.
"""

import os
import shutil
from pathlib import Path

from app.agent.memory import (
    _extract_memory_from_md,
    get_memory_context,
    is_memory_enabled,
    write_memory,
)


class TestMemoryInfrastructure:
    """Test memory I/O operations."""

    def setup_method(self):
        """Clean memories before each test."""
        memories_dir = Path(__file__).parent.parent / "memories"
        if memories_dir.exists():
            for f in memories_dir.glob("202*.md"):  # Only test files
                if "test_pipeline" in f.name:
                    f.unlink()

    def test_memory_disabled_by_default(self):
        """Memory should be disabled when TRACER_MEMORY_ENABLED not set."""
        # Ensure env var is not set
        os.environ.pop("TRACER_MEMORY_ENABLED", None)

        assert not is_memory_enabled()

        # get_memory_context should return empty string
        memory = get_memory_context("test_pipeline", "abc123")
        assert memory == ""

        # write_memory should return None
        result = write_memory(
            pipeline_name="test_pipeline",
            alert_id="abc123",
            root_cause="Test",
            confidence=0.9,
            validity_score=0.9,
        )
        assert result is None

    def test_write_and_read_memory(self):
        """Write a memory file and read it back."""
        os.environ["TRACER_MEMORY_ENABLED"] = "1"

        try:
            # Write memory
            filepath = write_memory(
                pipeline_name="test_pipeline",
                alert_id="abc12345",
                root_cause="External API schema change removed customer_id field",
                confidence=0.85,
                validity_score=0.90,
                action_sequence=["inspect_s3_object", "get_s3_object", "inspect_lambda_function"],
                data_lineage="External API → Lambda → S3 → Prefect",
                problem_pattern="Upstream schema failure causing validation errors",
            )

            assert filepath is not None
            assert filepath.exists()
            assert "test_pipeline" in filepath.name
            assert "abc12345" in filepath.name

            # Read it back
            memory = get_memory_context("test_pipeline", "abc12345")
            # New implementation returns minimal summary
            assert memory != ""
            assert "External API" in memory or "schema change" in memory.lower()
            assert "inspect_s3_object" in memory
            assert len(memory) < 1000  # Should be short and targeted

        finally:
            os.environ.pop("TRACER_MEMORY_ENABLED", None)

    def test_quality_gate_blocks_low_quality(self):
        """Memory should not be written if confidence or validity is too low."""
        os.environ["TRACER_MEMORY_ENABLED"] = "1"

        try:
            # Low confidence
            result = write_memory(
                pipeline_name="test_pipeline",
                alert_id="low001",
                root_cause="Unknown",
                confidence=0.50,
                validity_score=0.90,
            )
            assert result is None

            # Low validity
            result = write_memory(
                pipeline_name="test_pipeline",
                alert_id="low002",
                root_cause="Unknown",
                confidence=0.90,
                validity_score=0.50,
            )
            assert result is None

            # Both high - should write
            result = write_memory(
                pipeline_name="test_pipeline",
                alert_id="high001",
                root_cause="Test",
                confidence=0.85,
                validity_score=0.85,
            )
            assert result is not None

        finally:
            os.environ.pop("TRACER_MEMORY_ENABLED", None)

    def test_seed_from_md_files(self):
        """Load memory from cached investigations (new behavior: no direct seed)."""
        os.environ["TRACER_MEMORY_ENABLED"] = "1"

        try:
            # Write a prior investigation first
            from app.agent.memory import write_memory

            write_memory(
                pipeline_name="test_pipeline_seed",
                alert_id="seed001",
                root_cause="External API issue",
                confidence=0.85,
                validity_score=0.90,
                action_sequence=["inspect_s3_object"],
            )

            # Now get memory - should load the cached investigation
            memory = get_memory_context(pipeline_name="test_pipeline_seed")

            # Should contain minimal cached summary
            assert memory != ""
            assert "External API" in memory or "inspect_s3_object" in memory

        finally:
            os.environ.pop("TRACER_MEMORY_ENABLED", None)

    def test_extract_memory_from_md(self):
        """Test MD extraction heuristic."""
        sample_md = """
# Prefect ECS Architecture

## Components

- External API provides data
- Trigger Lambda ingests and writes to S3
- Prefect flow validates schema

## Failure Path

Missing field in schema causes validation failure.
The external API removed customer_id in v2.0.

Lambda writes data to S3 landing bucket.
"""

        extracted = _extract_memory_from_md(sample_md)

        # Should extract headings and bullets
        assert "# Prefect ECS Architecture" in extracted
        assert "## Components" in extracted
        assert "- External API" in extracted
        assert "- Trigger Lambda" in extracted

        # Should extract keyword lines
        assert "Missing field" in extracted or "schema" in extracted.lower()

    def test_multiple_prior_investigations_loaded(self):
        """Load multiple prior investigations for the same pipeline."""
        os.environ["TRACER_MEMORY_ENABLED"] = "1"

        try:
            # Write 3 prior investigations
            for i in range(3):
                write_memory(
                    pipeline_name="test_pipeline",
                    alert_id=f"prior{i:03d}",
                    root_cause=f"Root cause {i}",
                    confidence=0.80,
                    validity_score=0.80,
                )

            # Load memory - should load most recent investigation (minimal summary)
            memory = get_memory_context("test_pipeline")

            assert memory != ""
            # New behavior: loads single most recent investigation as minimal summary
            assert "Prior" in memory or "Root cause" in memory
            # Should be short
            assert len(memory) < 1000

        finally:
            os.environ.pop("TRACER_MEMORY_ENABLED", None)
