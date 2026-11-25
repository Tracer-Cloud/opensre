"""Action tools: read, search and list the files of the working directory.

Without them the agent read code by writing Python for the sandbox (ten of
sixteen recorded ``execute_python_code`` calls only read, listed or grepped
files, each behind an approval) or through ``shell_run``. These are read-only,
need no approval, and run together when one response carries several.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from config.constants.capabilities import SHELL_COMMANDS_CAPABILITY
from core.agent_harness.tools import capability_available_from_sources
from core.domain.types.tools import ToolSurface
from core.tool import (
    SideEffectLevel,
    ToolExecutionResult,
    tool_output_byte_budget,
    truncate_output_text,
)
from core.tool_framework import tool
from tools.system.workspace_files.access import FileAccessError, read_text, resolve_readable
from tools.system.workspace_files.candidates import glob_matcher, workspace_files
from tools.system.workspace_files.reading import DEFAULT_LINE_LIMIT, numbered_excerpt
from tools.system.workspace_files.searching import (
    DEFAULT_MATCH_LIMIT,
    MAX_CONTEXT_LINES,
    SEARCH_SECONDS,
    SearchPattern,
    SearchPatternError,
    matching_lines,
)
from tools.system.workspace_paths import workspace_relative

# The decorators below spell these names out: the static registry index reads
# a tool's name from a literal.
READ_FILE_TOOL = "read_file"
SEARCH_FILES_TOOL = "search_files"
LIST_FILES_TOOL = "list_files"

#: Paths one listing shows; the count beside them is always complete.
MAX_LISTED_PATHS = 200


def _reads_local_files(sources: dict[str, dict[str, Any]]) -> bool:
    """A host that offers no commands on its machine offers no reads of it either."""
    return capability_available_from_sources(sources, SHELL_COMMANDS_CAPABILITY)


def _refused(message: str, **fields: Any) -> dict[str, Any]:
    return {"ok": False, **fields, "error": message}


def _shown(text: str, details: dict[str, Any]) -> ToolExecutionResult:
    """The text the model reads, with a one-line ``summary`` for the transcript."""
    return ToolExecutionResult(
        content=truncate_output_text(text, tool_output_byte_budget()),
        details={"ok": True, **details},
        provider_output_bounded=True,
    )


def _directory(path: str) -> Path:
    """``path`` as a directory inside the working directory, or raise."""
    scope = resolve_readable(path.strip() or ".")
    if not scope.exists():
        raise FileAccessError(f"{path} does not exist.")
    if not scope.is_dir():
        raise FileAccessError(f"{path} is not a directory.")
    return scope


def _join(scope: Path, name: str) -> str:
    """A file under ``scope`` as the caller can pass it back: relative to the working directory."""
    base = workspace_relative(scope)
    return name if base == "." else f"{base}/{name}"


@tool(
    name="read_file",
    source="system",
    display_name="Read a file",
    description=(
        "Read a text file in the working directory as numbered lines "
        "(`<line number><tab><text>`). Use it instead of `cat`, `head` or `sed -n` through "
        "shell_run: it needs no approval, several reads sent in one response run "
        "together, and a long file pages with `offset` and `limit`. Files that hold "
        "credentials (`.env`, private keys) are refused and known token formats are "
        "redacted. Read-only."
    ),
    compact_description=(
        "Read a text file in the working directory as numbered lines; no approval, "
        "and several reads in one response run together."
    ),
    use_cases=[
        "Read a source file before explaining or changing it",
        "Read the lines around a match that search_files reported",
        "Read a config, workflow or documentation file",
    ],
    anti_examples=[
        "Counting entries of a YAML, JSON or TOML file (use read_structured_file)",
        "Finding which file holds something (use search_files)",
        "Reading a file outside the working directory (use shell_run)",
    ],
    outputs={
        "path": "The file, relative to the working directory",
        "first_line": "Number of the first line returned",
        "last_line": "Number of the last line returned",
        "total_lines": "How many lines the file has",
        "remaining_lines": "Lines after the excerpt; pass offset=last_line+1 to continue",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    is_available=_reads_local_files,
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File to read, relative to the working directory.",
            },
            "offset": {
                "type": "integer",
                "minimum": 1,
                "description": "First line to return, 1-based. Defaults to 1.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    f"Most lines to return. Defaults to {DEFAULT_LINE_LIMIT}; the output "
                    "budget may end the excerpt sooner."
                ),
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    tags=("safe",),
)
def read_file(
    path: str, offset: int = 1, limit: int = DEFAULT_LINE_LIMIT, **_kwargs: Any
) -> ToolExecutionResult | dict[str, Any]:
    """Return lines of ``path`` from ``offset``, numbered as in the file."""
    target = (path or "").strip()
    if not target:
        return _refused("path is required.")
    try:
        resolved = resolve_readable(target)
        if resolved.is_dir():
            raise FileAccessError(f"{target} is a directory; list it with list_files.")
        if not resolved.is_file():
            raise FileAccessError(f"{target} does not exist.")
        text = read_text(resolved)
    except FileAccessError as exc:
        return _refused(str(exc), path=target)
    shown = workspace_relative(resolved)
    excerpt = numbered_excerpt(
        text, offset=int(offset), limit=int(limit), max_bytes=tool_output_byte_budget() - 256
    )
    if not excerpt.text:
        note = "is empty" if excerpt.total_lines == 0 else f"has {excerpt.total_lines} lines"
        return _refused(f"{shown} {note}; nothing at line {excerpt.first_line}.", path=shown)
    header = f"{shown} (lines {excerpt.first_line}-{excerpt.last_line} of {excerpt.total_lines})"
    body = [header, excerpt.text]
    if excerpt.remaining_lines:
        body.append(
            f"[{excerpt.remaining_lines} more lines; continue with offset={excerpt.last_line + 1}]"
        )
    return _shown(
        "\n".join(body),
        {
            "path": shown,
            "first_line": excerpt.first_line,
            "last_line": excerpt.last_line,
            "total_lines": excerpt.total_lines,
            "remaining_lines": excerpt.remaining_lines,
            "summary": f"Read {header}",
        },
    )


@tool(
    name="search_files",
    source="system",
    display_name="Search files",
    description=(
        "Search file contents under the working directory with a Python regular "
        "expression and return each matching line as `path:line:text`, in path order. "
        "It covers the files git tracks plus the ones it does not ignore, so build "
        "output, caches and virtual environments are not searched. Use it instead of "
        "`grep` or `rg` through shell_run: it needs no approval, works where ripgrep "
        "is not installed, and several searches sent in one response run together. "
        "Narrow with `path` (a directory, or one file) and `glob` (`*.py`, "
        "`tests/**/test_*.py`); "
        "`context_lines` adds the lines around each match. Files that hold credentials "
        "are skipped. Read-only."
    ),
    compact_description=(
        "Search file contents under the working directory with a regular expression; "
        "returns path:line:text. No approval, no ripgrep needed."
    ),
    use_cases=[
        "Find where a function, class, constant or error message is defined or used",
        "Find every file that mentions a setting, env var or endpoint",
        "Check whether a pattern still occurs after a change",
    ],
    anti_examples=[
        "Finding files by name (use list_files with a pattern)",
        "Counting files (use count_files)",
        "Searching outside the working directory (use shell_run)",
    ],
    outputs={
        "match_count": "Matching lines returned",
        "file_count": "Files those lines are in",
        "truncated": (
            f"True when the search stopped at {DEFAULT_MATCH_LIMIT} matches or at its time "
            "limit; more may exist"
        ),
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    is_available=_reads_local_files,
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": (
                    "Python regular expression matched against each line, such as "
                    r"`def \w+_handler` or `TODO|FIXME`. Escape literal `.`, `(` and `[`."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "Directory or single file to search, relative to the working directory. "
                    "Defaults to the whole working directory."
                ),
            },
            "glob": {
                "type": "string",
                "description": (
                    "Only search files matching this glob. Without `/` it names files at "
                    "any depth (`*.py`); with `/` it is matched from `path` "
                    "(`tests/**/test_*.py`)."
                ),
            },
            "ignore_case": {"type": "boolean", "description": "Match case-insensitively."},
            "context_lines": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_CONTEXT_LINES,
                "description": "Lines to show before and after each match. Defaults to 0.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    tags=("safe",),
)
def search_files(
    pattern: str,
    path: str = ".",
    glob: str = "",
    ignore_case: bool = False,
    context_lines: int = 0,
    **_kwargs: Any,
) -> ToolExecutionResult | dict[str, Any]:
    """Return the lines under ``path`` that match ``pattern``."""
    if not (pattern or "").strip():
        return _refused("pattern is required.")
    try:
        compiled = SearchPattern(pattern, ignore_case=bool(ignore_case))
    except SearchPatternError as exc:
        return _refused(f"pattern is not a valid regular expression: {exc}", pattern=pattern)
    try:
        scope = resolve_readable((path or "").strip() or ".")
        if not scope.exists():
            raise FileAccessError(f"{path} does not exist.")
    except FileAccessError as exc:
        return _refused(str(exc), path=path)
    where = workspace_relative(scope)
    files: Iterable[tuple[str, Path]]
    if scope.is_file():
        # One file is a search scope too: "where in this module is X".
        wanted = None
        files = [(where, scope)]
        shown_path: Callable[[str], str] = str
    else:
        wanted = glob_matcher(glob) if (glob or "").strip() else None
        files = (
            (name, target)
            for name, target in workspace_files(scope)
            if wanted is None or wanted(name)
        )

        def shown_path(name: str) -> str:
            return _join(scope, name)

    page = matching_lines(
        files,
        compiled,
        shown_path=shown_path,
        limit=DEFAULT_MATCH_LIMIT,
        context=int(context_lines),
    )
    scoped = f" in files matching `{glob.strip()}`" if wanted is not None else ""
    place = f"in {where}" if scope.is_file() else f"under {where}{scoped}"
    stopped = (
        f"stopped after {SEARCH_SECONDS:g} seconds, before every file was searched: the "
        "pattern is slow on these files. Simplify it, or narrow the path or glob"
    )
    if not page.match_count:
        if page.timed_out:
            return _refused(f"The search {stopped}.", pattern=pattern)
        summary = f"No matches for `{pattern}` {place}"
        return _shown(
            summary, {"match_count": 0, "file_count": 0, "truncated": False, "summary": summary}
        )
    files_word = "file" if page.file_count == 1 else "files"
    lines_word = "line" if page.match_count == 1 else "lines"
    summary = (
        f"{page.match_count} matching {lines_word} in {page.file_count} {files_word} "
        f"for `{pattern}` {place}"
    )
    body = [summary, *page.lines]
    if page.truncated:
        body.append(
            f"[stopped at {page.match_count} matches; more may exist. Narrow the pattern, "
            "path or glob]"
        )
    if page.timed_out:
        body.append(f"[{stopped}]")
    return _shown(
        "\n".join(body),
        {
            "match_count": page.match_count,
            "file_count": page.file_count,
            "truncated": page.truncated or page.timed_out,
            "summary": summary,
        },
    )


@tool(
    name="list_files",
    source="system",
    display_name="List files",
    description=(
        "List files of the working directory. With only `path`, show what is directly "
        "inside that directory: its subdirectories with how many files each holds, then "
        "its files. With `pattern`, list every file under `path` whose path matches the "
        "glob (`*.py` at any depth, `docs/**/*.mdx` from `path`). It covers the files git "
        "tracks plus the ones it does not ignore, so build output and virtual "
        "environments are left out. Use it instead of `ls` or `find` through shell_run: "
        "it needs no approval. The count is of the whole tree; at most "
        f"{MAX_LISTED_PATHS} paths are shown. Read-only."
    ),
    compact_description=(
        "List a directory of the working directory, or the files under it that match a "
        "glob. No approval."
    ),
    use_cases=[
        "See how a repository or a package is laid out",
        "Find files by name or extension",
        "Find the tests or docs that sit beside a module",
    ],
    anti_examples=[
        "Counting files (use count_files, which returns the number alone)",
        "Searching file contents (use search_files)",
        "Listing a directory outside the working directory (use shell_run)",
    ],
    outputs={
        "count": "Files that matched, or entries directly inside the directory",
        "truncated": "True when more paths matched than are shown",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    is_available=_reads_local_files,
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list, relative to the working directory. Defaults to it.",
            },
            "pattern": {
                "type": "string",
                "description": (
                    "Glob of the files to list under `path`. Without `/` it names files at "
                    "any depth (`test_*.py`); with `/` it is matched from `path` "
                    "(`src/**/*.ts`). Omit to list only what is directly inside `path`."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    },
    tags=("safe",),
)
def list_files(
    path: str = ".", pattern: str = "", **_kwargs: Any
) -> ToolExecutionResult | dict[str, Any]:
    """List the entries of ``path``, or the files under it matching ``pattern``."""
    try:
        scope = _directory(path or ".")
    except FileAccessError as exc:
        return _refused(str(exc), path=path)
    where = workspace_relative(scope)
    names = [name for name, _target in workspace_files(scope)]
    if (pattern or "").strip():
        wanted = glob_matcher(pattern)
        matched = [_join(scope, name) for name in names if wanted(name)]
        files_word = "file" if len(matched) == 1 else "files"
        summary = f"{len(matched)} {files_word} match `{pattern.strip()}` under {where}"
        return _listing(summary, matched, count=len(matched))
    directories: dict[str, int] = {}
    files: list[str] = []
    for name in names:
        head, separator, _rest = name.partition("/")
        if separator:
            directories[head] = directories.get(head, 0) + 1
        else:
            files.append(name)
    entries = [
        *(
            f"{name}/ ({count} {'file' if count == 1 else 'files'})"
            for name, count in sorted(directories.items())
        ),
        *sorted(files),
    ]
    summary = f"{where}: {len(directories)} directories, {len(files)} files"
    return _listing(summary, entries, count=len(entries))


def _listing(summary: str, entries: list[str], *, count: int) -> ToolExecutionResult:
    shown = entries[:MAX_LISTED_PATHS]
    body = [summary, *shown]
    truncated = len(entries) > len(shown)
    if truncated:
        body.append(f"[{len(entries) - len(shown)} more not shown; narrow the path or pattern]")
    return _shown("\n".join(body), {"count": count, "truncated": truncated, "summary": summary})


__all__ = [
    "LIST_FILES_TOOL",
    "MAX_LISTED_PATHS",
    "READ_FILE_TOOL",
    "SEARCH_FILES_TOOL",
    "list_files",
    "read_file",
    "search_files",
]
