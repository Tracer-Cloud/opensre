"""The active repositories' AGENTS.md, loaded for the action prompt.

These are the instructions of the repository the turn works on, not OpenSRE's
own docs. The shared system prompt says the AGENTS.md chain from the repository
root to the working directory comes with the prompt; this module is what
delivers it.

For each active repository a verified local checkout comes first: every
directory from the git root down to the working directory contributes its
``AGENTS.override.md``, else its ``AGENTS.md`` (Codex's precedence). Without
one, the root ``AGENTS.md`` of the default branch is read through the vendor's
connection. One 32 KiB budget covers every file: the file that crosses it is
cut, and later files are named but left out. Text is decoded, stripped of
control characters, and redacted before it is counted. When nothing could be
read, one line says so and tells the model to read the file itself.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.constants.repository_instructions import (
    REPOSITORY_INSTRUCTIONS_CLOSE_TAG,
    REPOSITORY_INSTRUCTIONS_FILE_PREFIX,
    REPOSITORY_INSTRUCTIONS_FILENAME,
    REPOSITORY_INSTRUCTIONS_HEADER_PREFIX,
    REPOSITORY_INSTRUCTIONS_MAX_BYTES,
    REPOSITORY_INSTRUCTIONS_OPEN_TAG,
    REPOSITORY_INSTRUCTIONS_OVERRIDE_FILENAME,
    REPOSITORY_INSTRUCTIONS_READ_BYTES,
)
from infrastructure.harness_providers import (
    RemoteInstructionsStatus,
    checkout_matches_repository,
    fetch_repository_instructions,
)
from infrastructure.safety.secret_redaction import redact_text

logger = logging.getLogger(__name__)

#: Precedence within one directory: the local override replaces the shared file.
_FILENAMES = (REPOSITORY_INSTRUCTIONS_OVERRIDE_FILENAME, REPOSITORY_INSTRUCTIONS_FILENAME)
_BUDGET = f"{REPOSITORY_INSTRUCTIONS_MAX_BYTES // 1024} KiB"
#: The system prompt says AGENTS.md comes with the prompt; that holds only for
#: what this block loaded, so every gap says so where the model reads it.
_NOT_INCLUDED = (
    "These instructions are not included in this prompt, whatever the AGENTS.md "
    "section of the system prompt says;"
)
#: C0 and C1 controls except tab and newline; carriage returns become newlines first.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
#: A file must not close the wrapper and go on speaking as OpenSRE.
_CLOSING_TAG = re.compile(r"</\s*INSTRUCTIONS\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class _Candidate:
    """One instruction file found for a repository, before budgeting."""

    #: The directory the file governs, as the prompt names it.
    scope: str
    filename: str
    content: bytes


@dataclass(frozen=True)
class _InstructionFile:
    scope: str
    text: str


def repository_instructions_text(
    active_repositories: Mapping[str, str],
    *,
    resolved_integrations: Mapping[str, Any],
    working_directory: str | None = None,
) -> str:
    """Render each active repository's AGENTS.md, or why there is none; "" without one.

    ``active_repositories`` maps a VCS vendor to its active repository, as
    ``TurnSnapshot.active_vcs_repositories`` does. ``working_directory``
    defaults to the process's. Never raises: a repository whose instructions
    could not be read gets the line that tells the model to read them itself.
    """
    if not active_repositories:
        return ""
    directory = _working_directory(working_directory)
    remaining = REPOSITORY_INSTRUCTIONS_MAX_BYTES
    sections: list[str] = []
    for vendor in sorted(active_repositories):
        repository = active_repositories[vendor]
        try:
            section, used = _repository_section(
                vendor, repository, directory, resolved_integrations, remaining
            )
        except Exception:
            logger.debug("AGENTS.md for %s could not be loaded", repository, exc_info=True)
            section, used = _unchecked_line(repository, "the read failed"), 0
        sections.append(section)
        remaining -= used
    return "".join(("\n\n".join(sections), "\n\n"))


def _repository_section(
    vendor: str,
    repository: str,
    directory: Path | None,
    resolved_integrations: Mapping[str, Any],
    remaining: int,
) -> tuple[str, int]:
    """One repository's section and the budget bytes it used."""
    if directory is not None:
        root = _checkout_root(directory)
        if root is not None and checkout_matches_repository(vendor, repository, root):
            candidates, unreadable = _local_candidates(root, directory)
            return _loaded_section(repository, str(root), candidates, remaining, unreadable)
    remote = fetch_repository_instructions(vendor, repository, resolved_integrations)
    if remote.status is RemoteInstructionsStatus.FOUND:
        candidate = _Candidate(repository, REPOSITORY_INSTRUCTIONS_FILENAME, remote.content)
        return _loaded_section(repository, remote.origin, [candidate], remaining)
    if remote.status is RemoteInstructionsStatus.MISSING:
        return _missing_line(repository), 0
    return _unchecked_line(repository, remote.reason), 0


def _working_directory(value: str | None) -> Path | None:
    try:
        return Path(os.path.abspath(value)) if value else Path(os.getcwd())
    except OSError:
        # The process's working directory was removed.
        return None


def _checkout_root(directory: Path) -> Path | None:
    """The nearest ancestor holding ``.git`` (a directory, or a file in a worktree)."""
    for candidate in (directory, *directory.parents):
        if os.path.lexists(candidate / ".git"):
            return candidate
    return None


def _local_candidates(root: Path, directory: Path) -> tuple[list[_Candidate], list[str]]:
    """One file per directory from ``root`` down to ``directory``, or ``root``'s alone.

    Also returns the files that exist but could not be read, so the prompt can
    say they were skipped instead of implying there are none.
    """
    try:
        parts = directory.relative_to(root).parts
    except ValueError:
        parts = ()
    folders = [root]
    for part in parts:
        folders.append(folders[-1] / part)
    candidates: list[_Candidate] = []
    unreadable: list[str] = []
    for folder in folders:
        # ``isfile`` follows links (as Codex does) and skips FIFOs, which would block a read.
        name = next((name for name in _FILENAMES if os.path.isfile(folder / name)), None)
        if name is None:
            continue
        content = _read_head(folder / name)
        if content is None:
            unreadable.append(f"{name} in {_label(str(folder))}")
        else:
            candidates.append(_Candidate(_label(str(folder)), name, content))
    return candidates, unreadable


def _read_head(path: Path) -> bytes | None:
    """At most one byte past the read limit, so a longer file is known to be cut."""
    try:
        with path.open("rb") as stream:
            return stream.read(REPOSITORY_INSTRUCTIONS_READ_BYTES + 1)
    except OSError:
        return None


def _loaded_section(
    repository: str,
    origin: str,
    candidates: Sequence[_Candidate],
    remaining: int,
    unreadable: Sequence[str] = (),
) -> tuple[str, int]:
    """Render ``candidates`` within ``remaining`` bytes; the file that crosses it ends the budget.

    ``unreadable`` names files that exist but could not be read; they are listed
    as skipped, never treated as absent.
    """
    files: list[_InstructionFile] = []
    over_budget: list[str] = []
    used = 0
    for candidate in candidates:
        name = f"{candidate.filename} in {candidate.scope}"
        if used >= remaining:
            over_budget.append(f"{name} (left out)")
            continue
        text, size, truncated = _prompt_text(candidate.content, remaining - used)
        if text.strip():
            files.append(_InstructionFile(candidate.scope, text))
            used += size
            if truncated:
                over_budget.append(f"{name} (cut)")
        elif truncated:
            over_budget.append(f"{name} (left out)")
        if truncated:
            used = remaining
    if not files and unreadable:
        return _unchecked_line(repository, f"could not read {', '.join(unreadable)}"), used
    if not files and not over_budget:
        return _missing_line(repository), 0
    if not files:
        return _over_budget_line(repository, over_budget), used
    notes = []
    if over_budget:
        notes.append(f"Over OpenSRE's {_BUDGET} AGENTS.md budget: {', '.join(over_budget)}")
    if unreadable:
        notes.append(f"OpenSRE could not read {', '.join(unreadable)}")
    return _render_loaded(repository, _label(origin), files, notes), used


def _prompt_text(content: bytes, limit: int) -> tuple[str, int, bool]:
    """Decode, clean, and redact ``content``, then cut it to ``limit`` UTF-8 bytes.

    Returns the text, its size in bytes, and whether it was cut. Redaction runs
    before the cut, over the whole read, so a credential that crosses the budget
    is replaced whole rather than left half visible.
    """
    cut = len(content) > REPOSITORY_INSTRUCTIONS_READ_BYTES
    text = content[:REPOSITORY_INSTRUCTIONS_READ_BYTES].decode("utf-8", errors="replace")
    text = _CONTROL_CHARACTERS.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _CLOSING_TAG.sub("[/INSTRUCTIONS]", redact_text(text))
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, len(encoded), cut
    kept = encoded[:limit].decode("utf-8", errors="ignore")
    return kept, len(kept.encode("utf-8")), True


def _render_loaded(
    repository: str, origin: str, files: Sequence[_InstructionFile], notes: Sequence[str]
) -> str:
    """The header line, a note on what was left out if anything was, then one wrapper per file.

    The section ends at its last closing wrapper, which is how analytics exports
    find the text to leave out (``infrastructure.safety``); keep any note above
    the wrappers.
    """
    lines = [
        f"{REPOSITORY_INSTRUCTIONS_HEADER_PREFIX}{repository}, loaded by OpenSRE from {origin}):"
    ]
    if notes:
        lines.append(
            f"[{'; '.join(notes)}. {_NOT_INCLUDED} read them before changing files they cover.]"
        )
    lines.append(
        "\n\n".join(
            f"{REPOSITORY_INSTRUCTIONS_FILE_PREFIX}{item.scope}\n\n"
            f"{REPOSITORY_INSTRUCTIONS_OPEN_TAG}\n{item.text.rstrip()}\n"
            f"{REPOSITORY_INSTRUCTIONS_CLOSE_TAG}"
            for item in files
        )
    )
    return "\n".join(lines)


def _missing_line(repository: str) -> str:
    return f"REPOSITORY INSTRUCTIONS: no AGENTS.md found for {repository}."


def _over_budget_line(repository: str, over_budget: Sequence[str]) -> str:
    return (
        f"REPOSITORY INSTRUCTIONS: AGENTS.md for {repository} did not fit OpenSRE's "
        f"{_BUDGET} budget ({', '.join(over_budget)}). {_NOT_INCLUDED} read the "
        "repository's AGENTS.md before changing its files."
    )


def _unchecked_line(repository: str, reason: str) -> str:
    return (
        f"REPOSITORY INSTRUCTIONS: OpenSRE could not load AGENTS.md for {repository} "
        f"({reason}). {_NOT_INCLUDED} read the repository's AGENTS.md before changing its files."
    )


def _label(value: str) -> str:
    """A path as prompt text: no control character can start a line of its own."""
    return _CONTROL_CHARACTERS.sub("", value.replace("\n", " ").replace("\r", " "))


__all__ = ["repository_instructions_text"]
