"""Work out what a parsed patch does to the files, then write it.

A patch is planned whole before anything is written: every path is resolved,
every hunk is located, and a failure leaves the working directory as it was.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.system.workspace_files.access import MAX_READ_BYTES
from tools.system.workspace_files.patch_format import (
    ADDED,
    CONTEXT,
    AddFile,
    DeleteFile,
    FileOperation,
    Hunk,
    UpdateFile,
)

#: Expected lines quoted back when a hunk cannot be placed.
_MISSING_LINES_SHOWN = 12
_BINARY_PROBE_BYTES = 8192
#: Typographic characters a model writes as ASCII, mapped to what it wrote.
_TYPOGRAPHIC = str.maketrans(
    {
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys("‘’‚‛", "'"),
        **dict.fromkeys("“”„‟", '"'),
        **dict.fromkeys("           　", " "),
    }
)


def _plain(line: str) -> str:
    return line.translate(_TYPOGRAPHIC).strip()


#: How a hunk line is compared with a file line, strictest first. A model drops
#: trailing spaces, re-indents, and types quotes the file holds as curly ones.
_COMPARISONS: tuple[Callable[[str], str], ...] = (str, str.rstrip, str.strip, _plain)


class PatchApplyError(ValueError):
    """The patch does not fit the files; the message says which file and why."""


@dataclass(frozen=True)
class FileChange:
    """What the patch leaves at one path: ``A``\\ dded, ``M``\\ odified or ``D``\\ eleted."""

    kind: str
    path: str
    target: Path
    #: The file's new bytes; ``None`` deletes it.
    content: bytes | None


@dataclass(frozen=True)
class PatchPlan:
    """Every write a patch makes, with how many lines it adds and removes."""

    changes: tuple[FileChange, ...]
    added: int
    removed: int


def plan_patch(
    operations: Sequence[FileOperation],
    *,
    resolve: Callable[[str], Path],
    shown: Callable[[Path], str],
) -> PatchPlan:
    """Return the writes ``operations`` make, without making them.

    ``resolve`` turns a path of the patch into the file it may write and raises
    for one it may not; ``shown`` renders a file for messages. Operations see
    the result of the ones before them, so a patch may touch a file twice.
    """
    files = _PlannedFiles(shown)
    added = removed = 0
    for operation in operations:
        target = resolve(operation.path)
        name = shown(target)
        if isinstance(operation, AddFile):
            if files.text(target) is not None:
                raise PatchApplyError(
                    f"{name} already exists. Change it with '*** Update File: ', or delete it first."
                )
            files.put(target, _joined(operation.lines, "\n", trailing_newline=True))
            added += len(operation.lines)
        elif isinstance(operation, DeleteFile):
            removed += len(_lines(files.existing(target))[0])
            files.put(target, None)
        else:
            updated = _updated_text(files.existing(target), operation, name)
            added += sum(hunk.added for hunk in operation.hunks)
            removed += sum(hunk.removed for hunk in operation.hunks)
            destination = resolve(operation.move_to) if operation.move_to else target
            if destination != target:
                if files.text(destination) is not None:
                    raise PatchApplyError(
                        f"{shown(destination)} already exists; cannot move there."
                    )
                files.put(target, None)
            files.put(destination, updated)
    return PatchPlan(files.changes(), added, removed)


def write_plan(plan: PatchPlan) -> None:
    """Write every change of ``plan``; when one fails, put back what was written."""
    written: list[tuple[Path, bytes | None]] = []
    writing = ""
    try:
        for change in plan.changes:
            writing = change.path
            previous = change.target.read_bytes() if change.target.is_file() else None
            written.append((change.target, previous))
            if change.content is None:
                change.target.unlink()
            else:
                _write_file(change.target, change.content)
    except OSError as exc:
        for target, previous in reversed(written):
            with contextlib.suppress(OSError):
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    _write_file(target, previous)
        raise PatchApplyError(
            f"could not write {writing} ({type(exc).__name__}); every file was put back as it was."
        ) from exc


class _PlannedFiles:
    """The files as the patch leaves them so far, read from disk on first use."""

    def __init__(self, shown: Callable[[Path], str]) -> None:
        self._shown = shown
        self._planned: dict[Path, str | None] = {}
        self._on_disk: dict[Path, bytes | None] = {}

    def text(self, target: Path) -> str | None:
        """The file's text at this point of the patch, or ``None`` when it does not exist."""
        if target in self._planned:
            return self._planned[target]
        data = self._disk_bytes(target)
        if data is None:
            return None
        if b"\0" in data[:_BINARY_PROBE_BYTES]:
            raise PatchApplyError(f"{self._shown(target)} is a binary file.")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PatchApplyError(f"{self._shown(target)} is not UTF-8 text.") from exc

    def existing(self, target: Path) -> str:
        """The file's text, or raise when the patch names a file that is not there."""
        text = self.text(target)
        if text is None:
            raise PatchApplyError(f"{self._shown(target)} does not exist.")
        return text

    def put(self, target: Path, text: str | None) -> None:
        self._disk_bytes(target)
        self._planned[target] = text

    def changes(self) -> tuple[FileChange, ...]:
        """The paths whose content differs from the disk, in the order first touched."""
        changes: list[FileChange] = []
        for target, text in self._planned.items():
            before = self._on_disk[target]
            after = None if text is None else text.encode("utf-8")
            if before == after:
                continue
            kind = "D" if after is None else "A" if before is None else "M"
            changes.append(FileChange(kind, self._shown(target), target, after))
        return tuple(changes)

    def _disk_bytes(self, target: Path) -> bytes | None:
        if target not in self._on_disk:
            self._on_disk[target] = _read_bytes(target, self._shown(target))
        return self._on_disk[target]


def _read_bytes(target: Path, name: str) -> bytes | None:
    """The file's bytes, ``None`` when nothing is there, or raise for what is no file."""
    if not target.exists():
        return None
    if not target.is_file():
        raise PatchApplyError(f"{name} is not a file.")
    try:
        if target.stat().st_size > MAX_READ_BYTES:
            raise PatchApplyError(f"{name} is past the {MAX_READ_BYTES:,} byte limit.")
        return target.read_bytes()
    except OSError as exc:
        raise PatchApplyError(f"cannot read {name}: {type(exc).__name__}") from exc


def _lines(text: str) -> tuple[list[str], str, bool]:
    """The file's lines, its line ending, and whether the last line is terminated."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    terminated = lines[-1] == ""
    if terminated:
        lines.pop()
    return lines, newline, terminated


def _joined(lines: Sequence[str], newline: str, *, trailing_newline: bool) -> str:
    if not lines:
        return ""
    return newline.join(lines) + (newline if trailing_newline else "")


def _updated_text(text: str, operation: UpdateFile, name: str) -> str:
    """``text`` with the hunks of ``operation`` applied, keeping its line endings."""
    lines, newline, terminated = _lines(text)
    replacements: list[tuple[int, int, list[str]]] = []
    cursor = 0
    for hunk in operation.hunks:
        start, length, replacement = _placed(lines, hunk, cursor, name)
        replacements.append((start, length, replacement))
        cursor = start + length
    replacements.sort(key=lambda item: item[0])
    for (start, length, _), (next_start, _, _) in zip(replacements, replacements[1:], strict=False):
        if start + length > next_start:
            raise PatchApplyError(f"{name}: two hunks change the same lines.")
    for start, length, replacement in reversed(replacements):
        lines[start : start + length] = replacement
    return _joined(lines, newline, trailing_newline=terminated)


def _placed(lines: list[str], hunk: Hunk, cursor: int, name: str) -> tuple[int, int, list[str]]:
    """Where ``hunk`` applies: the first line it replaces, how many, and with what."""
    located = bool(hunk.headers)
    for header in hunk.headers:
        found = _find(lines, (header,), cursor, at_end=False)
        if found is None:
            raise PatchApplyError(f"{name}: could not find the context line {header!r}.")
        cursor = found + 1
    body = hunk.lines
    while True:
        old = [text for kind, text in body if kind != ADDED]
        if not old:
            # Only added lines: after the context the hunk named, else at the end.
            return (cursor if located else len(lines)), 0, [text for _kind, text in body]
        start = _find(lines, old, cursor, at_end=hunk.at_end_of_file)
        if start is None and cursor:
            # Hunks written out of file order still say what they mean.
            start = _find(lines, old, 0, at_end=hunk.at_end_of_file)
        if start is not None:
            return start, len(old), _replacement(lines, start, body)
        if body[-1] == (CONTEXT, "") and len(body) > 1:
            # A blank line before the next section is a separator, not context.
            body = body[:-1]
            continue
        quoted = "\n".join(hunk.old[:_MISSING_LINES_SHOWN])
        raise PatchApplyError(
            f"{name}: could not find these lines. Read the file again and copy them "
            f"exactly; it may have changed.\n{quoted}"
        )


def _replacement(lines: Sequence[str], start: int, body: Sequence[tuple[str, str]]) -> list[str]:
    """What the hunk leaves in place of the lines it matched.

    A context line keeps the file's own text. It may have matched loosely (the
    model dropped trailing spaces or typed a straight quote), and writing the
    model's spelling back would change a line the patch says it leaves alone.
    """
    replacement: list[str] = []
    position = start
    for kind, text in body:
        if kind == ADDED:
            replacement.append(text)
            continue
        if kind == CONTEXT:
            replacement.append(lines[position])
        position += 1
    return replacement


def _find(lines: Sequence[str], wanted: Sequence[str], start: int, *, at_end: bool) -> int | None:
    """Index of the first run of ``lines`` at or after ``start`` that reads as ``wanted``."""
    last = len(lines) - len(wanted)
    if last < start:
        return None
    for comparable in _COMPARISONS:
        pattern = [comparable(line) for line in wanted]
        candidates = (last, *range(start, last + 1)) if at_end else range(start, last + 1)
        for index in candidates:
            if comparable(lines[index]) == pattern[0] and all(
                comparable(lines[index + offset]) == pattern[offset]
                for offset in range(1, len(pattern))
            ):
                return index
    return None


def _write_file(target: Path, content: bytes) -> None:
    """Replace ``target`` with ``content`` in one step, keeping its permissions."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Nothing to replace: create it under the process umask.
        with open(target, "xb") as created:
            created.write(content)
        return
    mode = stat.S_IMODE(target.stat().st_mode)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(handle, "wb") as replacement:
            replacement.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


__all__ = ["FileChange", "PatchApplyError", "PatchPlan", "plan_patch", "write_plan"]
