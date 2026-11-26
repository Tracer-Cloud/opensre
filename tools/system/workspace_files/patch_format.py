"""Parse the patch text ``apply_patch`` takes into file operations.

The format is the file-oriented diff the system prompt tells the model to edit
with, which OpenAI models are trained to write::

    *** Begin Patch
    *** Add File: path            every following line starts with "+"
    *** Delete File: path
    *** Update File: path
    *** Move to: new/path         optional rename
    @@ optional context header
     context line
    -removed line
    +added line
    *** End of File               optional: the hunk sits at the end of the file
    *** End Patch

Parsing is lenient where models drift (a heredoc or code fence around the
patch, a first hunk with no ``@@``, a blank line with no leading space) and
strict where a guess could write the wrong thing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_DELETE = "*** Delete File: "
_UPDATE = "*** Update File: "
_MOVE = "*** Move to: "
_END_OF_FILE = "*** End of File"
_SECTION = "*** "
_HUNK = "@@"
#: ``apply_patch <<'EOF'``: the shell form of the same tool, which models still write.
_HEREDOC_OPEN = re.compile(r"^(?:apply_patch\s+)?<<-?\s*['\"]?(\w+)['\"]?$")
_FENCE = "```"


class PatchFormatError(ValueError):
    """The patch text does not follow the format; the message says where."""


#: The three kinds of hunk line, by the character each starts with.
CONTEXT, REMOVED, ADDED = " ", "-", "+"


@dataclass(frozen=True)
class Hunk:
    """One change inside a file.

    ``lines`` are ``(kind, text)`` pairs in patch order, ``kind`` being
    :data:`CONTEXT`, :data:`REMOVED` or :data:`ADDED`. ``headers`` are the
    ``@@`` lines that locate the hunk.
    """

    headers: tuple[str, ...]
    lines: tuple[tuple[str, str], ...]
    at_end_of_file: bool = False

    @property
    def old(self) -> tuple[str, ...]:
        """The lines the hunk expects in the file: its context and removed lines."""
        return tuple(text for kind, text in self.lines if kind != ADDED)

    @property
    def added(self) -> int:
        """How many lines the hunk adds."""
        return sum(kind == ADDED for kind, _text in self.lines)

    @property
    def removed(self) -> int:
        """How many lines the hunk removes."""
        return sum(kind == REMOVED for kind, _text in self.lines)


@dataclass(frozen=True)
class AddFile:
    """Create ``path`` with ``lines`` as its content."""

    path: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class DeleteFile:
    """Remove ``path``."""

    path: str


@dataclass(frozen=True)
class UpdateFile:
    """Apply ``hunks`` to ``path``, then rename it to ``move_to`` when given."""

    path: str
    hunks: tuple[Hunk, ...]
    move_to: str | None = None


FileOperation = AddFile | DeleteFile | UpdateFile


def parse_patch(text: str) -> tuple[FileOperation, ...]:
    """Return the file operations of ``text`` in order, or raise :class:`PatchFormatError`."""
    lines = _body_lines(text)
    operations: list[FileOperation] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(_ADD):
            added, index = _added_lines(lines, index + 1)
            operations.append(AddFile(_path(line, _ADD), added))
        elif line.startswith(_DELETE):
            operations.append(DeleteFile(_path(line, _DELETE)))
            index += 1
        elif line.startswith(_UPDATE):
            path = _path(line, _UPDATE)
            index += 1
            move_to = None
            if index < len(lines) and lines[index].startswith(_MOVE):
                move_to = _path(lines[index], _MOVE)
                index += 1
            hunks, index = _hunks(lines, index)
            if not hunks and move_to is None:
                raise PatchFormatError(f"*** Update File: {path} is followed by no change.")
            operations.append(UpdateFile(path, hunks, move_to))
        elif not line.strip():
            index += 1
        else:
            raise PatchFormatError(
                f"patch line {index + 2}: expected '*** Add File: ', '*** Delete File: ' or "
                f"'*** Update File: ', got {line!r}."
            )
    if not operations:
        raise PatchFormatError("The patch changes no file.")
    return tuple(operations)


def _body_lines(text: str) -> list[str]:
    """The lines between the begin and end markers."""
    lines = [line.removesuffix("\r") for line in text.strip().split("\n")]
    lines = _unwrapped(lines)
    if not lines or lines[0].strip() != _BEGIN:
        raise PatchFormatError(f"The patch must start with '{_BEGIN}'.")
    if len(lines) < 2 or lines[-1].strip() != _END:
        raise PatchFormatError(f"The patch must end with '{_END}'.")
    return lines[1:-1]


def _unwrapped(lines: list[str]) -> list[str]:
    """Drop a heredoc or a code fence a model wrapped the patch in."""
    if len(lines) < 3:
        return lines
    heredoc = _HEREDOC_OPEN.match(lines[0].strip())
    if heredoc is not None and lines[-1].strip() == heredoc.group(1):
        return lines[1:-1]
    if lines[0].startswith(_FENCE) and lines[-1].strip() == _FENCE:
        return lines[1:-1]
    return lines


def _path(line: str, marker: str) -> str:
    path = line[len(marker) :].strip()
    if not path:
        raise PatchFormatError(f"'{marker.strip()}' names no path.")
    return path


def _added_lines(lines: list[str], index: int) -> tuple[tuple[str, ...], int]:
    """The ``+`` lines of an added file, and the index after them."""
    added: list[str] = []
    content_length = 0
    while index < len(lines) and not lines[index].startswith(_SECTION):
        line = lines[index]
        if line.startswith("+"):
            added.append(line[1:])
            content_length = len(added)
        elif not line.strip():
            # A blank line the model left unprefixed: content between ``+``
            # lines, a separator after the last one.
            added.append("")
        else:
            raise PatchFormatError(
                f"patch line {index + 2}: every line of an added file starts with '+', "
                f"got {line!r}."
            )
        index += 1
    return tuple(added[:content_length]), index


def _hunks(lines: list[str], index: int) -> tuple[tuple[Hunk, ...], int]:
    """The hunks of one updated file, and the index of the next section."""
    hunks: list[Hunk] = []
    while index < len(lines):
        if lines[index].startswith(_SECTION) and lines[index].rstrip() != _END_OF_FILE:
            break
        headers: list[str] = []
        while index < len(lines) and lines[index].startswith(_HUNK):
            header = lines[index][len(_HUNK) :].strip()
            if header:
                headers.append(header)
            index += 1
        body: list[tuple[str, str]] = []
        at_end_of_file = False
        start = index
        # Unprefixed blank lines since the last prefixed one.
        unprefixed_blanks = 0
        while index < len(lines):
            line = lines[index]
            if line.rstrip() == _END_OF_FILE:
                at_end_of_file = True
                index += 1
                break
            if line.startswith((_HUNK, _SECTION)):
                break
            if line[:1] in (ADDED, REMOVED, CONTEXT):
                body.append((line[0], line[1:]))
                unprefixed_blanks = 0
            elif not line:
                # A blank context line often arrives without its leading space.
                body.append((CONTEXT, ""))
                unprefixed_blanks += 1
            else:
                raise PatchFormatError(
                    f"patch line {index + 2}: a hunk line starts with ' ', '-' or '+', "
                    f"got {line!r}."
                )
            index += 1
        kept = body[: len(body) - unprefixed_blanks]
        if (
            unprefixed_blanks
            and not at_end_of_file
            and kept
            and all(kind == ADDED for kind, _text in kept)
        ):
            # Blank lines before the next section separate it from a hunk of
            # only added lines. As context they would move it to the first
            # blank line of the file instead of its place.
            body = kept
        if not body:
            raise PatchFormatError(f"patch line {start + 2}: the hunk has no lines.")
        hunks.append(Hunk(tuple(headers), tuple(body), at_end_of_file))
    return tuple(hunks), index


__all__ = [
    "ADDED",
    "CONTEXT",
    "REMOVED",
    "AddFile",
    "DeleteFile",
    "FileOperation",
    "Hunk",
    "PatchFormatError",
    "UpdateFile",
    "parse_patch",
]
