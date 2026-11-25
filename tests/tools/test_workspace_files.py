"""Reading, searching and listing the working directory without an approval gate.

These tools replace model-written Python and shell reads, so they pin three
things: nothing outside the working directory or inside a credential file is
readable, what git ignores is neither listed nor searched, and the model reads
plain numbered text it can page through.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.llm.types import ToolCall
from core.tool import ToolExecutionResult
from core.tool.execution import execute_tool_calls
from tools.system.workspace_files.candidates import glob_matcher
from tools.system.workspace_files.tool import (
    LIST_FILES_TOOL,
    READ_FILE_TOOL,
    SEARCH_FILES_TOOL,
    list_files,
    read_file,
    search_files,
)

_TOKEN = "ghp_" + "a" * 36


@pytest.fixture(autouse=True)
def _work_where_the_files_are(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """These tools only read inside the working directory; point it at the fixture tree."""
    monkeypatch.chdir(tmp_path)


def _write(root: Path, name: str, text: str) -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _git_repository(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)


def _text(result: Any) -> str:
    assert isinstance(result, ToolExecutionResult), result
    assert isinstance(result.content, str)
    return result.content


def _refusal(result: Any) -> str:
    assert isinstance(result, dict), result
    assert result["ok"] is False
    return str(result["error"])


# --- what may be read -------------------------------------------------------


@pytest.mark.parametrize("path", ["/etc/hosts", "../outside.txt"])
def test_a_file_outside_the_working_directory_is_refused(tmp_path: Path, path: str) -> None:
    """No approval gate stands in front of a read, so the root must hold."""
    _write(tmp_path.parent, "outside.txt", "not yours")

    assert "outside the working directory" in _refusal(read_file(path=path))


def test_a_link_out_of_the_working_directory_is_judged_by_where_it_lands(tmp_path: Path) -> None:
    # Arrange: a file outside, reachable by a link inside.
    outside = _write(tmp_path.parent, "linked-secret.txt", "not yours")
    (tmp_path / "notes.txt").symlink_to(outside)
    _write(tmp_path, "real.txt", "not yours either way")

    # Act / Assert: not readable, not listed, not searched.
    assert "outside the working directory" in _refusal(read_file(path="notes.txt"))
    assert "notes.txt" not in _text(list_files())
    assert "notes.txt" not in _text(search_files(pattern="not yours"))


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.production",
        "deploy/server.pem",
        "id_ed25519",
        ".git/config",
        ".ssh/known",
        # A disk that ignores case opens these as the names above.
        ".ENV",
        "deploy/Server.PEM",
    ],
)
def test_a_file_that_holds_credentials_is_not_readable(tmp_path: Path, name: str) -> None:
    """A question about the code must not put a secret into the conversation."""
    _write(tmp_path, name, f"TOKEN={_TOKEN}\n")
    _write(tmp_path, "app.py", "TOKEN = load()\n")

    assert "holds credentials" in _refusal(read_file(path=name))
    found = _text(search_files(pattern="TOKEN"))
    assert "app.py:1:" in found
    assert name not in found
    assert name.rsplit("/", 1)[-1] not in _text(list_files(pattern="**/*"))


def test_a_committed_placeholder_for_a_credential_file_is_readable(tmp_path: Path) -> None:
    _write(tmp_path, ".env.example", "GITHUB_TOKEN=\n")

    assert "GITHUB_TOKEN=" in _text(read_file(path=".env.example"))


def test_a_token_written_into_source_is_redacted_from_a_read_and_a_search(tmp_path: Path) -> None:
    _write(tmp_path, "client.py", f'HEADERS = {{"Authorization": "token {_TOKEN}"}}\n')

    read = _text(read_file(path="client.py"))
    found = _text(search_files(pattern="Authorization"))

    assert _TOKEN not in read and "[REDACTED:github_pat]" in read
    assert _TOKEN not in found and "[REDACTED:github_pat]" in found


def test_a_binary_file_is_refused_and_skipped(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"needle\0\x01\x02")
    _write(tmp_path, "note.txt", "needle\n")

    assert "binary file" in _refusal(read_file(path="blob.bin"))
    assert "blob.bin" not in _text(search_files(pattern="needle"))


# --- reading ----------------------------------------------------------------


def test_a_read_numbers_lines_as_they_are_in_the_file_and_pages(tmp_path: Path) -> None:
    # Arrange
    _write(tmp_path, "pkg/mod.py", "".join(f"line {n}\n" for n in range(1, 11)))

    # Act
    first = read_file(path="pkg/mod.py", limit=3)
    rest = read_file(path="pkg/mod.py", offset=9)

    # Assert: the header says where the excerpt sits, the footer how to go on.
    assert _text(first).splitlines() == [
        "pkg/mod.py (lines 1-3 of 10)",
        "     1\tline 1",
        "     2\tline 2",
        "     3\tline 3",
        "[7 more lines; continue with offset=4]",
    ]
    assert _text(rest).splitlines() == [
        "pkg/mod.py (lines 9-10 of 10)",
        "     9\tline 9",
        "    10\tline 10",
    ]
    assert first.details["remaining_lines"] == 7
    assert first.details["summary"] == "Read pkg/mod.py (lines 1-3 of 10)"


def test_a_read_and_a_search_number_lines_as_an_editor_does(tmp_path: Path) -> None:
    """A form feed is not a line break: counting it as one shifts every later number."""
    _write(tmp_path, "legacy.py", "first = 1\n\x0c\nthird = 3\n")

    assert "     3\tthird = 3" in _text(read_file(path="legacy.py"))
    assert "legacy.py:3:third = 3" in _text(search_files(pattern="third"))


def test_a_read_past_the_output_budget_stops_at_a_whole_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cut mid-file by the generic truncation, the model lost the middle without a line to resume at."""
    # Arrange: 400 lines against a budget of about 600 bytes.
    monkeypatch.setenv("OPENSRE_TOOL_OUTPUT_TOKEN_LIMIT", "150")
    _write(tmp_path, "long.txt", "".join(f"row {n:04d}\n" for n in range(1, 401)))

    # Act
    result = read_file(path="long.txt")

    # Assert
    text = _text(result)
    last = result.details["last_line"]
    assert 1 < last < 400
    assert text.splitlines()[-1] == f"[{400 - last} more lines; continue with offset={last + 1}]"
    assert "truncated" not in text


@pytest.mark.parametrize(
    ("path", "offset", "expected"),
    [
        ("missing.py", 1, "does not exist"),
        ("pkg", 1, "is a directory; list it with list_files"),
        ("empty.txt", 1, "is empty"),
        ("short.txt", 50, "has 2 lines; nothing at line 50"),
    ],
)
def test_a_read_that_returns_nothing_says_why(
    tmp_path: Path, path: str, offset: int, expected: str
) -> None:
    (tmp_path / "pkg").mkdir()
    _write(tmp_path, "empty.txt", "")
    _write(tmp_path, "short.txt", "a\nb\n")

    assert expected in _refusal(read_file(path=path, offset=offset))


# --- searching --------------------------------------------------------------


def test_a_search_skips_what_git_ignores(tmp_path: Path) -> None:
    """A walk of this repository reads 72,000 files and reports a match sixteen times."""
    # Arrange: the source, an ignored build copy of it, and an untracked new file.
    _git_repository(tmp_path)
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "src/app.py", "def handler():\n    pass\n")
    _write(tmp_path, "build/lib/src/app.py", "def handler():\n    pass\n")
    _write(tmp_path, "src/new_module.py", "def handler_two():\n    pass\n")

    # Act
    found = search_files(pattern=r"def handler")
    listed = list_files(pattern="*.py")

    # Assert: tracked or not, but never ignored.
    assert _text(found).splitlines() == [
        "2 matching lines in 2 files for `def handler` under .",
        "src/app.py:1:def handler():",
        "src/new_module.py:1:def handler_two():",
    ]
    assert _text(listed).splitlines()[1:] == ["src/app.py", "src/new_module.py"]


def test_outside_a_git_repository_generated_directories_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: no repository here, and none above it that git could find.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    _write(tmp_path, "src/app.py", "needle\n")
    _write(tmp_path, "node_modules/dep/index.js", "needle\n")
    _write(tmp_path, "src/__pycache__/app.cpython-313.pyc", "needle\n")

    # Act / Assert
    assert _text(search_files(pattern="needle")).splitlines()[1:] == ["src/app.py:1:needle"]


def test_a_search_narrows_by_directory_and_glob_and_shows_context(tmp_path: Path) -> None:
    # Arrange
    _write(tmp_path, "src/a.py", "import os\nVALUE = 1\nprint(VALUE)\n")
    _write(tmp_path, "src/a.md", "VALUE = docs\n")
    _write(tmp_path, "tests/test_a.py", "VALUE = 2\n")

    # Act
    result = search_files(
        pattern=r"^value\s*=", path="src", glob="*.py", ignore_case=True, context_lines=1
    )

    # Assert: paths stay relative to the working directory, so they can be read back.
    assert _text(result).splitlines() == [
        r"1 matching line in 1 file for `^value\s*=` under src in files matching `*.py`",
        "src/a.py-1-import os",
        "src/a.py:2:VALUE = 1",
        "src/a.py-3-print(VALUE)",
    ]


def test_a_search_of_one_file_names_it_as_the_path(tmp_path: Path) -> None:
    """Models ask "where in this module": a file as ``path`` is a scope, not an error."""
    _write(tmp_path, "pkg/mod.py", "alpha = 1\nneedle = 2\n")
    _write(tmp_path, "pkg/other.py", "needle = 3\n")
    _write(tmp_path, ".env", "needle=secret\n")

    found = _text(search_files(pattern="needle", path="pkg/mod.py"))

    assert "pkg/mod.py:2:needle = 2" in found
    assert "other.py" not in found
    assert "holds credentials" in _refusal(search_files(pattern="needle", path=".env"))
    assert "does not exist" in _refusal(search_files(pattern="needle", path="pkg/gone.py"))


def test_a_search_stops_at_its_limit_and_says_more_may_exist(tmp_path: Path) -> None:
    _write(tmp_path, "many.txt", "hit\n" * 130)

    result = search_files(pattern="hit")

    assert result.details["match_count"] == 100
    assert result.details["truncated"] is True
    assert _text(result).splitlines()[-1] == (
        "[stopped at 100 matches; more may exist. Narrow the pattern, path or glob]"
    )


def test_a_search_that_finds_nothing_or_cannot_run_says_so(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "alpha\n")

    assert _text(search_files(pattern="omega")) == "No matches for `omega` under ."
    assert "not a valid regular expression" in _refusal(search_files(pattern="("))
    assert "is not a directory" in _refusal(list_files(path="a.txt"))


# --- listing ----------------------------------------------------------------


def test_a_directory_listing_shows_subdirectories_with_their_size_then_files(
    tmp_path: Path,
) -> None:
    # Arrange
    _write(tmp_path, "README.md", "")
    _write(tmp_path, "src/a.py", "")
    _write(tmp_path, "src/deep/b.py", "")
    _write(tmp_path, "tests/test_a.py", "")

    # Act / Assert
    assert _text(list_files()).splitlines() == [
        ".: 2 directories, 1 files",
        "src/ (2 files)",
        "tests/ (1 file)",
        "README.md",
    ]
    assert _text(list_files(path="src")).splitlines() == [
        "src: 1 directories, 1 files",
        "deep/ (1 file)",
        "a.py",
    ]


@pytest.mark.parametrize(
    ("pattern", "matches", "misses"),
    [
        ("*.py", ["a.py", "src/deep/b.py"], ["a.pyc", "README.md"]),
        ("src/*.py", ["src/a.py"], ["src/deep/b.py", "a.py"]),
        ("src/**/*.py", ["src/a.py", "src/deep/b.py"], ["tests/a.py"]),
        ("**/test_?.py", ["test_a.py", "tests/unit/test_b.py"], ["tests/test_ab.py"]),
        ("docs/[!_]*.md", ["docs/intro.md"], ["docs/_draft.md", "docs/sub/intro.md"]),
        ("test_[0-9].py", ["test_1.py", "tests/test_7.py"], ["test_a.py", "test_-.py"]),
        ("./docs/", ["docs/intro.md", "docs/sub/intro.md"], ["intro.md"]),
    ],
)
def test_a_glob_reads_the_way_gitignore_does(
    pattern: str, matches: list[str], misses: list[str]
) -> None:
    """``*`` stays inside one directory; a pattern without ``/`` names files at any depth."""
    wanted = glob_matcher(pattern)

    assert [path for path in matches if not wanted(path)] == []
    assert [path for path in misses if wanted(path)] == []


def test_a_long_listing_is_cut_but_its_count_is_whole(tmp_path: Path) -> None:
    for number in range(205):
        _write(tmp_path, f"data/row_{number:03d}.txt", "")

    result = list_files(pattern="*.txt")

    lines = _text(result).splitlines()
    assert lines[0] == "205 files match `*.txt` under ."
    assert lines[-1] == "[5 more not shown; narrow the path or pattern]"
    assert result.details == {
        "ok": True,
        "count": 205,
        "truncated": True,
        "summary": "205 files match `*.txt` under .",
    }


# --- how the agent reaches them ---------------------------------------------


def test_the_tools_are_registered_read_only_and_safe_to_run_together() -> None:
    from tools.registry import get_registered_tool

    for name in (READ_FILE_TOOL, SEARCH_FILES_TOOL, LIST_FILES_TOOL):
        registered = get_registered_tool(name)
        assert registered is not None, name
        assert registered.side_effect_level == "read_only"
        assert registered.requires_approval is False
        assert registered.concurrency_safe is True
        assert "action" in registered.surfaces


def test_one_response_reads_several_files_through_the_executor(tmp_path: Path) -> None:
    """The loop's own path: validated arguments in, one bounded text observation per call out."""
    # Arrange
    from tools.registry import get_registered_tool

    _write(tmp_path, "a.py", "ALPHA = 1\n")
    _write(tmp_path, "b.py", "BETA = 2\n")
    tools = [get_registered_tool(name) for name in (READ_FILE_TOOL, SEARCH_FILES_TOOL)]
    calls = [
        ToolCall(id="call_1", name=READ_FILE_TOOL, input={"path": "a.py"}),
        ToolCall(id="call_2", name=READ_FILE_TOOL, input={"path": "b.py"}),
        ToolCall(id="call_3", name=SEARCH_FILES_TOOL, input={"pattern": "BETA"}),
        ToolCall(id="call_4", name=READ_FILE_TOOL, input={"path": ".env"}),
    ]

    # Act
    results = execute_tool_calls(calls, tools, {})  # type: ignore[arg-type]

    # Assert
    assert [result.is_error for result in results] == [False, False, False, True]
    assert results[0].provider_content() == "a.py (lines 1-1 of 1)\n     1\tALPHA = 1"
    assert "b.py:1:BETA = 2" in str(results[2].provider_content())
    assert "holds credentials" in str(results[3].provider_content())
