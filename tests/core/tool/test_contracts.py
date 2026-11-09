"""Unit tests for core.tool.contracts (ToolMetadata contract)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.types.retrieval import RetrievalControls
from core.tool.contracts import EvidenceType, SideEffectLevel, ToolMetadata


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "my_tool",
        "description": "Does something useful.",
        "input_schema": {"type": "object", "properties": {}},
        "source": "grafana",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Valid construction
# ---------------------------------------------------------------------------


def test_tool_metadata_valid_minimal() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs())
    assert meta.name == "my_tool"
    assert meta.description == "Does something useful."
    assert meta.source == "grafana"
    assert meta.display_name is None


def test_tool_metadata_strips_surrounding_whitespace() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(name="  padded  ", description="  desc  "))
    assert meta.name == "padded"
    assert meta.description == "desc"


def test_tool_metadata_display_name_none_is_accepted() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(display_name=None))
    assert meta.display_name is None


def test_tool_metadata_display_name_set() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(display_name="My Tool"))
    assert meta.display_name == "My Tool"


def test_tool_metadata_optional_lists_default_empty() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs())
    assert meta.use_cases == []
    assert meta.examples == []
    assert meta.anti_examples == []
    assert meta.requires == []
    assert meta.outputs == {}
    assert meta.injected_params == []


def test_tool_metadata_retrieval_controls_defaults_to_zero_value() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs())
    assert isinstance(meta.retrieval_controls, RetrievalControls)


# ---------------------------------------------------------------------------
# Name / description validation
# ---------------------------------------------------------------------------


def test_tool_metadata_blank_name_rejected() -> None:
    with pytest.raises(ValidationError, match="name"):
        ToolMetadata.model_validate(_valid_kwargs(name="   "))


def test_tool_metadata_blank_description_rejected() -> None:
    with pytest.raises(ValidationError, match="description"):
        ToolMetadata.model_validate(_valid_kwargs(description=""))


def test_tool_metadata_blank_display_name_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolMetadata.model_validate(_valid_kwargs(display_name="  "))


# ---------------------------------------------------------------------------
# SideEffectLevel / EvidenceType literal validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["none", "read_only", "mutating", "external"])
def test_side_effect_level_valid_literals(level: SideEffectLevel) -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(side_effect_level=level))
    assert meta.side_effect_level == level


def test_side_effect_level_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        ToolMetadata.model_validate(_valid_kwargs(side_effect_level="destructive"))


@pytest.mark.parametrize(
    "et",
    [
        "logs",
        "metrics",
        "traces",
        "events",
        "topology",
        "deployment_metadata",
        "query_stats",
        "artifact",
        "other",
    ],
)
def test_evidence_type_valid_literals(et: EvidenceType) -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(evidence_type=et))
    assert meta.evidence_type == et


def test_evidence_type_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        ToolMetadata.model_validate(_valid_kwargs(evidence_type="profiling"))


def _run_mypy_snippet(code: str) -> str:
    """Helper to run mypy.api.run on a Python code snippet in an isolated temporary directory."""
    import tempfile
    from pathlib import Path

    api = pytest.importorskip("mypy.api")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        snippet_file = tmp_path / "snippet.py"
        cache_dir = tmp_path / ".mypy_cache"
        snippet_file.write_text(code, encoding="utf-8")

        stdout, _stderr, _exit_code = api.run([str(snippet_file), "--cache-dir", str(cache_dir)])
        return stdout


def test_static_typechecking_rejects_side_effect_level_typos() -> None:
    """Verify that static typechecking with mypy fails on invalid side_effect_level strings."""
    code = """from core.tool.contracts import BaseTool

class Tool(BaseTool):
    side_effect_level = "reed_only"
"""
    stdout = _run_mypy_snippet(code)
    assert "Incompatible types in assignment" in stdout, stdout


def test_static_typechecking_rejects_evidence_type_typos() -> None:
    """Verify that static typechecking with mypy fails on invalid evidence_type strings."""
    code = """from core.tool.contracts import BaseTool

class Tool(BaseTool):
    evidence_type = "metricz"
"""
    stdout = _run_mypy_snippet(code)
    assert "Incompatible types in assignment" in stdout, stdout


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Blocked on Phase B: BaseTool.surfaces is ClassVar[tuple[ToolSurface | str, ...]] "
        "until Phase B migrates tool surface declarations to ToolSurface enums."
    ),
)
def test_static_typechecking_rejects_surfaces_typos() -> None:
    """Verify static typechecking for surfaces typos.

    Currently xfail: BaseTool.surfaces intentionally permits str until Phase B.
    """
    code = """from core.tool.contracts import BaseTool

class Tool(BaseTool):
    surfaces = ("actoin",)
"""
    stdout = _run_mypy_snippet(code)
    assert "Incompatible types in assignment" in stdout, stdout
