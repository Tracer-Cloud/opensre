"""Counting from a parsed file, which is where hand-parsed shell output went wrong."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.system.structured_file.parse import StructureError, describe
from tools.system.structured_file.tool import TOOL_NAME, read_structured_file


@pytest.fixture(autouse=True)
def _work_where_the_files_are(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """These tools only read inside the working directory; point it at the fixture tree."""
    monkeypatch.chdir(tmp_path)


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


def test_a_bare_yaml_word_key_keeps_the_spelling_the_file_shows(tmp_path: Path) -> None:
    """YAML 1.1 reads a bare ``on:`` as the boolean True, which hides and collapses keys."""
    # Arrange
    path = _workflow(tmp_path)

    # Act
    triggers = describe(path, "on")
    top_level = describe(path)

    # Assert: reachable by name, and listed by name rather than as ``True``.
    assert triggers.count == 2
    assert triggers.keys == ("push", "pull_request")
    assert "on" in top_level.keys
    assert "True" not in top_level.keys


def test_two_yaml_word_keys_do_not_collapse_into_one(tmp_path: Path) -> None:
    """``on`` and ``yes`` both resolve to True, which would undercount the mapping."""
    # Arrange
    path = tmp_path / "flags.yml"
    path.write_text("on: 1\nyes: 2\noff: 3\n")

    # Act
    view = describe(path)

    # Assert
    assert view.count == 3
    assert view.keys == ("on", "yes", "off")


def test_a_scalar_value_is_never_returned(tmp_path: Path) -> None:
    """A config file may hold a token; this tool reports structure, not contents."""
    # Arrange
    path = tmp_path / "config.toml"
    path.write_text('[auth]\ntoken = "super-secret-value"\n')

    # Act
    result = read_structured_file(path=str(path), key="auth.token")

    # Assert
    assert "super-secret-value" not in json.dumps(result)
    assert result["response_text"] == "`auth.token` holds a single str value"


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


def test_a_merged_anchor_is_expanded(tmp_path: Path) -> None:
    """``<<`` pulls an anchored mapping in; skipping it made the file unreadable."""
    # Arrange: two jobs that share defaults through an anchor.
    path = tmp_path / "anchored.yml"
    path.write_text(
        "defaults: &defaults\n"
        "  runs-on: ubuntu-latest\n"
        "jobs:\n"
        "  build:\n"
        "    <<: *defaults\n"
        "    steps: []\n"
        "  test:\n"
        "    <<: *defaults\n"
        "    steps: []\n"
    )

    # Act
    jobs = describe(path, "jobs")
    build = describe(path, "jobs.build")

    # Assert: the file loads, and the merged key is part of the job.
    assert jobs.count == 2
    assert build.keys == ("runs-on", "steps")


def test_a_file_outside_the_working_directory_is_refused() -> None:
    """Key names from a private config would otherwise be readable."""
    # Arrange / Act / Assert
    with pytest.raises(StructureError, match="outside the working directory"):
        describe(Path("/etc/hosts"))
