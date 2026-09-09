"""Counting from a parsed file, which is where hand-parsed shell output went wrong."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.system.structured_file.parse import StructureError, describe
from tools.system.structured_file.tool import TOOL_NAME, read_structured_file

_WORKFLOW = """
name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo build
    env:
      MODE: fast
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
"""


def _workflow(tmp_path: Path) -> Path:
    path = tmp_path / "ci.yml"
    path.write_text(_WORKFLOW)
    return path


def test_a_job_count_ignores_nested_keys(tmp_path: Path) -> None:
    """The regex over two-space keys counted steps, env and permissions as jobs."""
    # Arrange: two jobs, each with nested keys at the same indentation.
    path = _workflow(tmp_path)

    # Act
    view = describe(path, "jobs")

    # Assert
    assert view.kind == "mapping"
    assert view.count == 2
    assert view.keys == ("build", "test")


def test_the_workflow_trigger_key_is_reachable(tmp_path: Path) -> None:
    """YAML reads a bare ``on:`` as the boolean True, hiding it from a plain lookup."""
    # Arrange
    path = _workflow(tmp_path)

    # Act
    view = describe(path, "on")

    # Assert
    assert view.count == 2
    assert view.keys == ("push", "pull_request")


def test_a_dotted_path_walks_into_a_toml_table(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "x"\ndependencies = ["a", "b", "c"]\n')

    # Act
    view = describe(path, "project.dependencies")

    # Assert
    assert view.kind == "list"
    assert view.count == 3


def test_a_key_whose_own_name_contains_a_dot_wins_over_the_path(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"a.b": [1, 2], "a": {"b": [1]}}))

    # Act
    view = describe(path, "a.b")

    # Assert: the literal key is matched before the dot separates it.
    assert view.count == 2


def test_an_unknown_key_says_so(tmp_path: Path) -> None:
    # Arrange
    path = _workflow(tmp_path)

    # Act / Assert
    with pytest.raises(StructureError, match="not in this file"):
        describe(path, "nope")


def test_an_unsupported_suffix_is_refused(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "notes.md"
    path.write_text("# hello")

    # Act / Assert
    with pytest.raises(StructureError, match="not one of"):
        describe(path)


def test_invalid_content_names_the_format_without_leaking_detail(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "broken.json"
    path.write_text("{not json")

    # Act / Assert
    with pytest.raises(StructureError, match="not valid json"):
        describe(path)


def test_the_tool_reports_the_count_in_one_sentence(tmp_path: Path) -> None:
    # Arrange
    path = _workflow(tmp_path)

    # Act
    result = read_structured_file(path=str(path), key="jobs")

    # Assert
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["response_text"].startswith("2 entries under `jobs`")
    assert "build, test" in result["response_text"]


def test_the_tool_is_registered_and_read_only() -> None:
    # Arrange / Act
    from tools.registry import get_registered_tool

    registered = get_registered_tool(TOOL_NAME)

    # Assert
    assert registered is not None
    assert registered.side_effect_level == "read_only"
    assert "path" in registered.public_input_schema["properties"]
