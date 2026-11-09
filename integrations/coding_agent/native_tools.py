"""Checkout-confined file and shell tools for OpenSRE's built-in coding agent.

Every path must resolve inside the checkout (no ``..``, no symlink out, nothing
written under ``.git``); ``bash`` runs there with a filtered environment and
refuses git verbs that commit, push, or rewrite history, since the caller ships
the diff. A bad request returns ``{"ok": False, "error": ...}`` for the model to
correct, and once the run's deadline passes every tool refuses so the model
stops and summarizes.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.tool import RegisteredTool
from core.tool_framework.utils import object_schema
from integrations.coding_agent.models import Progress
from integrations.git import git_invocations
from integrations.llm_cli.agent_exec import poll_agent_process
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env

_READ_LINES = 2000
_LINE_CHARS = 2000
_OUTPUT_CHARS = 30000
_LIST_LIMIT = 200
_COMMAND_TIMEOUT_SEC = 120
_COMMAND_TIMEOUT_MAX_SEC = 600
_GIT_TIMEOUT_SEC = 30

#: Commits, pushes, moves branches, or discards work; the caller owns the first two.
_REFUSED_GIT_VERBS = frozenset(
    {
        "am",
        "checkout",
        "cherry-pick",
        "clean",
        "commit",
        "merge",
        "pull",
        "push",
        "rebase",
        "reset",
        "restore",
        "revert",
        "stash",
        "switch",
        "worktree",
    }
)
#: Non-interactive like CI, and no bytecode in the diff the caller commits.
_COMMAND_ENV = {
    "CI": "true",
    "NO_COLOR": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}
_DEADLINE = (
    "The time budget is used up. Stop calling tools and write your final summary: what "
    "you changed, what you verified, and what is left."
)

ToolRun = Callable[..., dict[str, Any]]


class _Refused(ValueError):
    """A request the model can correct: bad path, unread file, ambiguous edit."""


class WorkspaceTools:
    """One run's tools, bound to its checkout, deadline, and progress line."""

    def __init__(
        self, workspace: str, *, deadline: float, on_progress: Progress | None = None
    ) -> None:
        self._root = Path(workspace).expanduser().resolve()
        self._deadline = deadline
        self._progress = on_progress or (lambda _step: None)
        self._read: set[Path] = set()
        self._shell = shutil.which("bash") or "/bin/sh"
        self._env = build_cli_subprocess_env(_COMMAND_ENV)
        self.deadline_reached = False

    def registered_tools(self) -> list[RegisteredTool]:
        """The six tools, as the model sees them."""
        integer, boolean, text = {"type": "integer"}, {"type": "boolean"}, {"type": "string"}
        specs: list[tuple[str, str, dict[str, Any], tuple[str, ...], ToolRun]] = [
            (
                "read_file",
                "Read a text file as numbered lines (number, tab, text). Read a file before "
                "editing or overwriting it; use offset/limit (1-based) for long files.",
                {"path": _path(), "offset": integer, "limit": integer},
                ("path",),
                self.read_file,
            ),
            (
                "edit_file",
                "Replace an exact string in a file you have read. old_string must match the "
                "file exactly, including indentation and without read_file's line-number "
                "prefix, and be unique unless replace_all is true.",
                {
                    "path": _path(),
                    "old_string": _path(),
                    "new_string": text,
                    "replace_all": boolean,
                },
                ("path", "old_string", "new_string"),
                self.edit_file,
            ),
            (
                "write_file",
                "Create a file, or overwrite one you have read, with the complete content "
                "given. Prefer edit_file for changes to an existing file.",
                {"path": _path(), "content": text},
                ("path", "content"),
                self.write_file,
            ),
            (
                "glob",
                "List files matching a glob, newest first. '*' stays in one directory; use "
                "'**/' for subdirectories (e.g. '**/*.py'). Ignored files are skipped.",
                {"pattern": _path()},
                ("pattern",),
                self.glob,
            ),
            (
                "grep",
                "Search file contents with an extended regex; returns path:line:text. Optional "
                "path (file or directory) and glob (e.g. '**/*.ts') narrow the search.",
                {"pattern": _path(), "path": text, "glob": text, "ignore_case": boolean},
                ("pattern",),
                self.grep,
            ),
            (
                "bash",
                "Run a shell command in the checkout root: the failing test, a linter, type "
                "check, build, or dependency install. Returns exit_code, stdout and stderr. "
                "Runs non-interactively with CI=true; git commit, push, reset, checkout and "
                "other history-changing git commands are refused.",
                {"command": _path(), "timeout_seconds": integer},
                ("command",),
                self.bash,
            ),
        ]
        return [
            RegisteredTool(
                name=name,
                description=description,
                input_schema=object_schema(properties=properties, required=required),
                source="coding_agent",
                run=self._guarded(run),
            )
            for name, description, properties, required, run in specs
        ]

    def _guarded(self, run: ToolRun) -> ToolRun:
        """Turn refusals and I/O failures into an error result; refuse everything past the deadline."""

        def guarded(**kwargs: Any) -> dict[str, Any]:
            if time.monotonic() >= self._deadline:
                self.deadline_reached = True
            if self.deadline_reached:
                return {"ok": False, "error": _DEADLINE}
            try:
                return run(**kwargs)
            except (ValueError, OSError, UnicodeDecodeError, subprocess.SubprocessError) as exc:
                return {"ok": False, "error": str(exc)}

        return guarded

    def read_file(self, path: str, offset: int = 1, limit: int = _READ_LINES) -> dict[str, Any]:
        target = self._inside(path)
        if not target.is_file():
            raise _Refused(f"{self._rel(target)} is not a file.")
        data = target.read_bytes()
        if b"\0" in data[:8192]:
            raise _Refused(f"{self._rel(target)} is a binary file.")
        self._read.add(target)
        self._progress(f"Reading {self._rel(target)}")
        lines = data.decode("utf-8", errors="replace").splitlines()
        start = max(int(offset), 1)
        count = min(max(int(limit), 1), _READ_LINES)
        chunk = lines[start - 1 : start - 1 + count]
        return {
            "ok": True,
            "path": self._rel(target),
            "content": "\n".join(
                f"{n:>6}\t{line[:_LINE_CHARS]}" for n, line in enumerate(chunk, start)
            ),
            "total_lines": len(lines),
            "truncated": start - 1 + count < len(lines),
        }

    def edit_file(
        self, path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> dict[str, Any]:
        target = self._inside(path, write=True)
        self._require_read(target)
        text = _read_text(target)
        if old_string not in text and "\r\n" in text:
            old_string = old_string.replace("\n", "\r\n")
            new_string = new_string.replace("\n", "\r\n")
        count = text.count(old_string)
        if count == 0:
            raise _Refused(
                f"old_string was not found in {self._rel(target)}; read it again and copy the "
                "exact text."
            )
        if count > 1 and not replace_all:
            raise _Refused(
                f"old_string occurs {count} times in {self._rel(target)}; add surrounding lines "
                "or set replace_all."
            )
        _write_text(target, text.replace(old_string, new_string, -1 if replace_all else 1))
        self._progress(f"Editing {self._rel(target)}")
        return {"ok": True, "path": self._rel(target), "replacements": count if replace_all else 1}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        target = self._inside(path, write=True)
        if target.exists():
            self._require_read(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_text(target, content)
        self._read.add(target)
        self._progress(f"Writing {self._rel(target)}")
        return {"ok": True, "path": self._rel(target)}

    def glob(self, pattern: str) -> dict[str, Any]:
        listed = self._git("ls-files", "-z", "-co", "--exclude-standard", "--", f":(glob){pattern}")
        found = [self._root / name for name in listed.split("\0") if name]
        files = sorted(
            (f for f in found if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True
        )
        return {
            "ok": True,
            "paths": [self._rel(f) for f in files[:_LIST_LIMIT]],
            "total": len(files),
        }

    def grep(
        self, pattern: str, path: str = "", glob: str = "", ignore_case: bool = False
    ) -> dict[str, Any]:
        scope = self._rel(self._inside(path)) if path.strip() else "."
        prefix = "" if scope == "." else f"{scope}/"
        pathspec = f":(glob){prefix}{glob.strip()}" if glob.strip() else scope
        flags = ["-i"] if ignore_case else []
        out = self._git("grep", "-nI", "--untracked", "-E", *flags, "-e", pattern, "--", pathspec)
        lines = out.splitlines()
        return {
            "ok": True,
            "matches": [line[:_LINE_CHARS] for line in lines[:_LIST_LIMIT]],
            "total": len(lines),
        }

    def bash(self, command: str, timeout_seconds: int = _COMMAND_TIMEOUT_SEC) -> dict[str, Any]:
        refused = next((v for _ws, v in git_invocations(command) if v in _REFUSED_GIT_VERBS), None)
        if refused:
            raise _Refused(
                f"git {refused} is not allowed: OpenSRE commits and pushes the result itself. "
                "Leave your changes in the working tree."
            )
        remaining = max(int(self._deadline - time.monotonic()), 1)
        timeout = min(max(int(timeout_seconds), 1), _COMMAND_TIMEOUT_MAX_SEC, remaining)
        self._progress(f"Running {' '.join(command.split())[:80]}")
        outcome = poll_agent_process(
            [self._shell, "-c", command], cwd=str(self._root), env=self._env, timeout_sec=timeout
        )
        if outcome.spawn_error:
            raise _Refused(outcome.spawn_error)
        return {
            "ok": outcome.returncode == 0 and not outcome.timed_out,
            "exit_code": outcome.returncode,
            "timed_out": outcome.timed_out,
            "stdout": _tail(outcome.stdout),
            "stderr": _tail(outcome.stderr),
        }

    def _inside(self, path: str, *, write: bool = False) -> Path:
        """Resolve *path* (symlinks included) inside the checkout, or refuse it."""
        if not path.strip():
            raise _Refused("A path is required.")
        target = (self._root / Path(path.strip()).expanduser()).resolve()
        if target != self._root and self._root not in target.parents:
            raise _Refused(f"{path} is outside the checkout; only its files can be used.")
        if write and target.relative_to(self._root).parts[:1] == (".git",):
            raise _Refused("Files under .git cannot be written.")
        return target

    def _require_read(self, target: Path) -> None:
        if target not in self._read:
            raise _Refused(f"Read {self._rel(target)} with read_file before changing it.")

    def _rel(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix() or "."

    def _git(self, *args: str) -> str:
        """Stdout of a read-only git query in the checkout; no match is empty, a failure refuses."""
        result = subprocess.run(
            ["git", *args],
            cwd=self._root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise _Refused(result.stderr.strip() or f"git {args[0]} failed.")
        return result.stdout


def _path() -> dict[str, Any]:
    """A fresh non-empty string schema; a shared dict would be mutated by schema normalizers."""
    return {"type": "string", "minLength": 1}


def _tail(text: str) -> str:
    """Failures print at the end, so long output keeps its tail."""
    if len(text) <= _OUTPUT_CHARS:
        return text
    return f"… [{len(text) - _OUTPUT_CHARS} characters omitted]\n{text[-_OUTPUT_CHARS:]}"


def _read_text(target: Path) -> str:
    # newline="" keeps CRLF line endings exactly as they are on disk.
    with target.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text(target: Path, content: str) -> None:
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)


__all__ = ["WorkspaceTools"]
