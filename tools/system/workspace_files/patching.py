"""Work out what a parsed patch does to the files, then write it.

A patch is planned whole before anything is written: every path is resolved,
every hunk is located, and a failure leaves the working directory as it was.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import stat
from collections.abc import Callable, Iterable, Sequence
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
    #: The file's bytes when the patch was planned; ``None`` when it was not there.
    before: bytes | None
    #: The file's new bytes; ``None`` deletes it.
    content: bytes | None
    #: Permission bits a new file takes over from the file it was renamed from.
    mode: int | None = None


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
                files.keep_mode(destination, of=target)
            files.put(destination, updated)
    return PatchPlan(files.changes(), added, removed)


def write_plan(plan: PatchPlan) -> None:
    """Make every change of ``plan``, or none of them.

    The plan was made from the files as they were, and an approval prompt may
    have stood open since: a file that changed meanwhile stops the write. The
    new contents are then written beside their targets, so running out of
    space fails before any file is touched. Each file is swapped in by rename
    with its original kept aside until all are in place; undoing is renames
    too, and a path that could not be put back is named.
    """
    for change in plan.changes:
        if _read_bytes(change.target, change.path) != change.before:
            raise PatchApplyError(
                f"{change.path} changed after the patch was read. Nothing was written; "
                "read the file again."
            )
    staged = _staged(plan.changes)
    done: list[tuple[FileChange, Path | None]] = []
    writing = ""
    try:
        for change in plan.changes:
            writing = change.path
            done.append((change, _set_aside(change) if change.before is not None else None))
            if change.content is not None:
                os.replace(staged[change.target], change.target)
                del staged[change.target]
    except OSError as exc:
        _discard(staged.values())
        lost = _undone(done)
        outcome = (
            "these files could not be put back and need checking: " + ", ".join(lost)
            if lost
            else "every file was put back as it was"
        )
        raise PatchApplyError(
            f"could not write {writing} ({type(exc).__name__}); {outcome}."
        ) from exc
    _discard(original for _change, original in done if original is not None)


def _staged(changes: Sequence[FileChange]) -> dict[Path, Path]:
    """Write each new content to a file beside its target; none on any failure."""
    staged: dict[Path, Path] = {}
    try:
        for change in changes:
            if change.content is None:
                continue
            target = change.target
            target.parent.mkdir(parents=True, exist_ok=True)
            beside = _beside(target, "new")
            staged[target] = beside
            # Created under the process umask, like any file the user makes.
            with os.fdopen(
                os.open(beside, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666), "wb"
            ) as out:
                out.write(change.content)
            mode = change.mode if change.before is None else stat.S_IMODE(target.stat().st_mode)
            if mode is not None:
                os.chmod(beside, mode)
    except OSError as exc:
        _discard(staged.values())
        raise PatchApplyError(
            f"could not write {change.path} ({type(exc).__name__}). Nothing was changed."
        ) from exc
    return staged


def _set_aside(change: FileChange) -> Path:
    """Keep the file's original reachable under another name until the patch is in."""
    original = _beside(change.target, "orig")
    if change.content is None:
        os.replace(change.target, original)
        return original
    try:
        # A second name for the same file: the target is never missing, even
        # if the process stops between here and the swap.
        os.link(change.target, original)
    except OSError:
        try:
            shutil.copy2(change.target, original)
        except OSError:
            _discard((original,))
            raise
    return original


def _undone(done: Sequence[tuple[FileChange, Path | None]]) -> list[str]:
    """Put back what was swapped in, last first; return the paths that would not go back."""
    lost: list[str] = []
    for change, original in reversed(done):
        try:
            if original is None:
                change.target.unlink(missing_ok=True)
            else:
                os.replace(original, change.target)
                # Renaming one name of a file over another name of the same
                # file does nothing, which is the case when the swap never
                # happened; the spare name still has to go.
                original.unlink(missing_ok=True)
        except OSError:
            lost.append(change.path)
    return lost


def _beside(target: Path, role: str) -> Path:
    return target.with_name(f".{target.name}.{secrets.token_hex(4)}.{role}")


def _discard(paths: Iterable[Path]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


class _PlannedFiles:
    """The files as the patch leaves them so far, read from disk on first use."""

    def __init__(self, shown: Callable[[Path], str]) -> None:
        self._shown = shown
        self._planned: dict[Path, str | None] = {}
        self._on_disk: dict[Path, bytes | None] = {}
        self._modes: dict[Path, int] = {}

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

    def keep_mode(self, target: Path, *, of: Path) -> None:
        """A renamed file keeps the permissions of the file it was: a script stays runnable."""
        if of in self._modes:
            self._modes[target] = self._modes[of]
        elif of.is_file():
            self._modes[target] = stat.S_IMODE(of.stat().st_mode)

    def changes(self) -> tuple[FileChange, ...]:
        """The paths whose content differs from the disk, in the order first touched."""
        changes: list[FileChange] = []
        for target, text in self._planned.items():
            before = self._on_disk[target]
            after = None if text is None else text.encode("utf-8")
            if before == after:
                continue
            kind = "D" if after is None else "A" if before is None else "M"
            changes.append(
                FileChange(
                    kind, self._shown(target), target, before, after, self._modes.get(target)
                )
            )
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


def _lines(text: str) -> tuple[list[str], list[str], bool]:
    """The file's lines, each line's own ending, and whether the last line is terminated.

    An unterminated last line has the ending ``""``.
    """
    pieces = text.split("\n")
    terminated = pieces[-1] == ""
    if terminated:
        pieces.pop()
    lines: list[str] = []
    endings: list[str] = []
    for index, piece in enumerate(pieces):
        if index == len(pieces) - 1 and not terminated:
            lines.append(piece)
            endings.append("")
        elif piece.endswith("\r"):
            lines.append(piece[:-1])
            endings.append("\r\n")
        else:
            lines.append(piece)
            endings.append("\n")
    return lines, endings, terminated


def _usual_ending(endings: Sequence[str]) -> str:
    """The ending most of the file's lines use: what a line the patch adds gets."""
    windows = endings.count("\r\n")
    return "\r\n" if windows > endings.count("\n") else "\n"


def _joined(lines: Sequence[str], newline: str, *, trailing_newline: bool) -> str:
    if not lines:
        return ""
    return newline.join(lines) + (newline if trailing_newline else "")


def _updated_text(text: str, operation: UpdateFile, name: str) -> str:
    """``text`` with the hunks of ``operation`` applied, keeping its line endings.

    Every line the patch leaves keeps its own ending, so editing one line of a
    file with mixed endings does not rewrite the others.
    """
    lines, endings, terminated = _lines(text)
    newline = _usual_ending(endings)
    replacements: list[tuple[int, int, list[tuple[str, str]]]] = []
    cursor = 0
    for hunk in operation.hunks:
        start, length, replacement = _placed(lines, endings, newline, hunk, cursor, name)
        replacements.append((start, length, replacement))
        cursor = start + length
    replacements.sort(key=lambda item: item[0])
    for (start, length, _), (next_start, _, _) in zip(replacements, replacements[1:], strict=False):
        if start + length > next_start:
            raise PatchApplyError(f"{name}: two hunks change the same lines.")
    for start, length, replacement in reversed(replacements):
        lines[start : start + length] = [line for line, _ending in replacement]
        endings[start : start + length] = [ending for _line, ending in replacement]
    last = len(lines) - 1
    return "".join(
        line + ("" if index == last and not terminated else ending or newline)
        for index, (line, ending) in enumerate(zip(lines, endings, strict=True))
    )


def _placed(
    lines: list[str],
    endings: Sequence[str],
    newline: str,
    hunk: Hunk,
    cursor: int,
    name: str,
) -> tuple[int, int, list[tuple[str, str]]]:
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
            return (cursor if located else len(lines)), 0, [(text, newline) for _kind, text in body]
        # Never before the cursor: lines that repeat earlier in the file would
        # take a change meant for the place the hunk's header named.
        start = _find(lines, old, cursor, at_end=hunk.at_end_of_file)
        if start is not None:
            return start, len(old), _replacement(lines, endings, newline, start, body)
        if body[-1] == (CONTEXT, "") and len(body) > 1:
            # A blank line before the next section is a separator, not context.
            body = body[:-1]
            continue
        quoted = "\n".join(hunk.old[:_MISSING_LINES_SHOWN])
        raise PatchApplyError(
            f"{name}: could not find these lines. Read the file again and copy them "
            f"exactly; it may have changed.\n{quoted}"
        )


def _replacement(
    lines: Sequence[str],
    endings: Sequence[str],
    newline: str,
    start: int,
    body: Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    """What the hunk leaves in place of the lines it matched, each with its ending.

    A context line keeps the file's own text and ending. It may have matched
    loosely (the model dropped trailing spaces or typed a straight quote), and
    writing the model's spelling back would change a line the patch says it
    leaves alone. An added line takes the file's usual ending.
    """
    replacement: list[tuple[str, str]] = []
    position = start
    for kind, text in body:
        if kind == ADDED:
            replacement.append((text, newline))
            continue
        if kind == CONTEXT:
            replacement.append((lines[position], endings[position]))
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


__all__ = ["FileChange", "PatchApplyError", "PatchPlan", "plan_patch", "write_plan"]
