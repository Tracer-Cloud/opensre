"""Counting project files, where `find` counted compiled caches as test files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import tools.system.file_count.count as count_module
from tools.system.file_count.count import FileCountError, count_matching_files
from tools.system.file_count.tool import TOOL_NAME, count_files


@pytest.fixture(autouse=True)
def _work_where_the_files_are(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """These tools only read inside the working directory; point it at the fixture tree."""
    monkeypatch.chdir(tmp_path)


def _tree(root: Path) -> None:
    """Two real test modules plus the caches a build leaves behind."""
    (root / "pkg").mkdir()
    (root / "pkg" / "test_one.py").write_text("")
    (root / "pkg" / "test_two.py").write_text("")
    (root / "pkg" / "helper.py").write_text("")
    cache = root / "pkg" / "__pycache__"
    cache.mkdir()
    (cache / "test_one.cpython-311.pyc").write_text("")
    (cache / "test_two.cpython-311.pyc").write_text("")
    venv = root / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "test_vendored.py").write_text("")


def test_generated_directories_are_not_counted(tmp_path: Path) -> None:
    """`test_*` matched `.pyc` files in __pycache__, inflating 103 into 233."""
    # Arrange
    _tree(tmp_path)

    # Act
    tally = count_matching_files(tmp_path, "test_*.py")

    # Assert: the two real modules, not the caches or the vendored copy.
    assert tally.count == 2
    assert tally.skipped_directories == 2


def test_the_default_pattern_counts_every_project_file(tmp_path: Path) -> None:
    # Arrange
    _tree(tmp_path)

    # Act
    tally = count_matching_files(tmp_path)

    # Assert
    assert tally.count == 3


def test_a_missing_directory_says_so_instead_of_returning_zero(tmp_path: Path) -> None:
    # Arrange / Act / Assert
    with pytest.raises(FileCountError, match="does not exist"):
        count_matching_files(tmp_path / "nope")


def test_a_file_path_is_refused(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "one.py"
    path.write_text("")

    # Act / Assert
    with pytest.raises(FileCountError, match="not a directory"):
        count_matching_files(path)


def test_a_symlinked_directory_is_not_followed(tmp_path: Path) -> None:
    """A link back up the tree would otherwise count forever."""
    # Arrange
    _tree(tmp_path)
    (tmp_path / "pkg" / "loop").symlink_to(tmp_path, target_is_directory=True)

    # Act
    tally = count_matching_files(tmp_path, "test_*.py")

    # Assert
    assert tally.count == 2


def test_the_tool_names_the_glob_it_counted(tmp_path: Path) -> None:
    # Arrange
    _tree(tmp_path)

    # Act
    result = count_files(path=str(tmp_path), pattern="test_*.py")

    # Assert
    assert result["ok"] is True
    assert result["count"] == 2
    # The glob is fenced: unfenced, `*test*` reaches the reader as emphasis.
    assert "2 files matching `test_*.py`" in result["response_text"]
    assert "not counting generated directories" in result["response_text"]


def test_the_tool_is_registered_and_read_only() -> None:
    # Arrange / Act
    from tools.registry import get_registered_tool

    registered = get_registered_tool(TOOL_NAME)

    # Assert
    assert registered is not None
    assert registered.side_effect_level == "read_only"
    assert set(registered.public_input_schema["properties"]) == {"path", "pattern"}


def test_a_path_outside_the_working_directory_is_refused(tmp_path: Path) -> None:
    """Read-only tools run without an approval gate, so the root must stay inside."""
    # Arrange / Act / Assert
    with pytest.raises(FileCountError, match="outside the working directory"):
        count_matching_files(Path("/etc"))
    with pytest.raises(FileCountError, match="outside the working directory"):
        count_matching_files(Path("../.."))


def test_a_symlinked_root_is_judged_by_where_it_lands(tmp_path: Path, monkeypatch) -> None:
    # Arrange: a link inside the workspace pointing outside it.
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    monkeypatch.chdir(workspace)

    # Act / Assert
    with pytest.raises(FileCountError, match="outside the working directory"):
        count_matching_files(Path("escape"))


def test_an_unreadable_subtree_raises_instead_of_undercounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skipped subtree would make an exact-looking count partial.

    The walk error is simulated rather than produced with ``chmod``: as root,
    or on a filesystem that ignores the mode, an unreadable directory is still
    readable and the test would pass for the wrong reason.
    """
    # Arrange
    _tree(tmp_path)

    def _walk_that_fails(top: Any, onerror: Any = None, followlinks: bool = False) -> Any:
        denied = PermissionError(13, "Permission denied")
        denied.filename = str(Path(top) / "locked")
        if onerror is not None:
            onerror(denied)
        return iter(())

    monkeypatch.setattr(count_module.os, "walk", _walk_that_fails)

    # Act / Assert
    with pytest.raises(FileCountError, match="cannot read"):
        count_matching_files(Path("."), "test_*.py")


def test_the_reported_path_stays_relative_to_the_workspace(tmp_path: Path) -> None:
    """Resolving for safety turns a path absolute; the answer should not show that."""
    # Arrange
    _tree(tmp_path)

    # Act
    result = count_files(path="pkg", pattern="test_*.py")

    # Assert: no home directory in the sentence the reader sees.
    assert "under pkg," in result["response_text"]
    assert str(tmp_path) not in result["response_text"]
    assert all(not name.startswith("/") for name in result["names"])
