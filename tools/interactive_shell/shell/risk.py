"""Coarse, advisory risk classification for a shell command.

Turns a command that already needs approval into a ``(risk, why)`` pair for
the confirmation card. The grade does not control approval; even commands that
look read-only may have effects after shell expansion.

The classifier is deliberately conservative: an unrecognized command is
``MEDIUM``, and anything matching a destructive or remote pattern is ``HIGH``.
It never reports ``LOW`` for a command it does not positively recognize as a
small, local, reversible write.
"""

from __future__ import annotations

import re
from enum import StrEnum

_OPERATOR_SPLIT = re.compile(r"\|\||&&|[|;\n]|>>|>|<|&")


class CommandRisk(StrEnum):
    """How much a mutating command can hurt if approved."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Substrings that mark a remote, irreversible, or destructive action regardless
# of the leading verb (checked against the whole command, lowercased).
_HIGH_RISK_PATTERNS: tuple[str, ...] = (
    "git push",
    "git reset --hard",
    "git clean",
    "git rebase",
    "kubectl delete",
    "docker rm",
    "docker rmi",
    "drop table",
    "drop database",
    "> /dev/",
    "chmod -r",
    "chown -r",
    "sudo ",
)
# Commands that reach the network / a remote system.
_NETWORK_VERBS = frozenset(
    {"curl", "wget", "ssh", "scp", "rsync", "kubectl", "helm", "aws", "gcloud", "terraform"}
)
# Verbs that change installed software.
_PACKAGE_VERBS = frozenset({"pip", "pip3", "uv", "npm", "yarn", "pnpm", "brew", "apt", "cargo"})
# Small, local, reversible writes.
_CREATE_VERBS = frozenset({"mkdir", "touch"})
_EDIT_VERBS = frozenset({"cp", "mv", "ln", "sed", "tee"})


def _first_verb(segment: str) -> str:
    """The executable name of a command segment, ignoring env-var prefixes."""
    for token in segment.strip().split():
        if "=" in token and not token.startswith("-"):
            continue  # skip ENV=val prefixes
        return token.rsplit("/", 1)[-1]
    return ""


def _args_after_verb(segment: str) -> list[str]:
    """Tokens after the command verb, skipping any leading ``ENV=val`` prefixes."""
    tokens = segment.strip().split()
    seen_verb = False
    args: list[str] = []
    for token in tokens:
        if not seen_verb:
            if "=" in token and not token.startswith("-"):
                continue  # env prefix precedes the verb
            seen_verb = True  # this token is the verb itself
            continue
        args.append(token)
    return args


def _segments(command: str) -> list[str]:
    return [seg for seg in _OPERATOR_SPLIT.split(command) if seg.strip()]


_RM_VERBS = frozenset({"rm", "rmdir", "unlink"})
_HARD_DESTRUCTIVE_VERBS = frozenset({"shred", "srm", "dd", "mkfs", "fdisk", "truncate"})
_RM_RECURSIVE_FLAGS = frozenset({"-r", "-R", "-rf", "-fr", "-rF", "-Rf", "-fR", "--recursive"})


def _looks_like_directory(target: str) -> bool:
    """A path with no filename extension (or a trailing slash) reads as a dir."""
    return target.endswith("/") or "." not in target.rstrip("/").rsplit("/", 1)[-1]


def _is_broad_path(target: str) -> bool:
    """A root-ish or home-root target whose deletion is not bounded."""
    normalized = target.rstrip("/")
    if normalized in {"", "~", "."}:
        return True
    return normalized.startswith(("/", "~")) and normalized.count("/") <= 1


def _delete_risk(command: str) -> tuple[CommandRisk, str] | None:
    """Grade a delete command by its target, or ``None`` if it is not one.

    Deleting a single explicitly-named file is bounded (medium); globs,
    recursive directory removals, root-ish paths, and low-level wipes are not.
    """
    for segment in _segments(command):
        verb = _first_verb(segment)
        if verb in _HARD_DESTRUCTIVE_VERBS:
            return CommandRisk.HIGH, "Destroys data at a low level; irreversible."
        if verb not in _RM_VERBS:
            continue
        args = _args_after_verb(segment)
        targets = [token for token in args if not token.startswith("-")]
        recursive = any(token in _RM_RECURSIVE_FLAGS for token in args)
        if any(char in target for target in targets for char in "*?["):
            return CommandRisk.HIGH, "Deletes multiple files via a glob; irreversible."
        if any(_is_broad_path(target) for target in targets):
            return CommandRisk.HIGH, "Deletes a broad or system path; irreversible."
        if not targets or (recursive and any(_looks_like_directory(t) for t in targets)):
            return CommandRisk.HIGH, "Recursively deletes a directory; irreversible."
        return CommandRisk.MEDIUM, "Deletes a single named file; recoverable only from a backup."
    return None


def classify_command_risk(command: str) -> tuple[CommandRisk, str]:
    """Return the risk level and a one-line human impact for ``command``.

    ``command`` is the full shell string the user is being asked to approve; a
    leading ``$ `` display prompt is stripped so it is not read as the verb.
    """
    command = command.strip()
    if command.startswith("$"):
        command = command[1:].lstrip()
    lowered = command.lower()
    if any(pattern in lowered for pattern in _HIGH_RISK_PATTERNS):
        return CommandRisk.HIGH, "Destructive or remote action that may be irreversible."

    delete = _delete_risk(command)
    if delete is not None:
        return delete

    verbs = [_first_verb(seg) for seg in _segments(command)]
    verbs = [verb for verb in verbs if verb]

    if any(verb in _NETWORK_VERBS for verb in verbs):
        return CommandRisk.HIGH, "Contacts a remote system or network resource."
    if any(verb in _PACKAGE_VERBS for verb in verbs):
        return CommandRisk.MEDIUM, "Changes installed packages or the environment."

    # A single ``>`` can clobber an existing file, and in-place edits change
    # existing data — reversible in principle, but not a no-op.
    overwrites = re.search(r"(?<!>)>(?!>)", command) is not None
    if overwrites or any(verb in _EDIT_VERBS for verb in verbs):
        return CommandRisk.MEDIUM, "Creates, overwrites, or edits local files."
    if ">>" in command or any(verb in _CREATE_VERBS for verb in verbs):
        return CommandRisk.LOW, "Creates new files or appends to them; reversible."

    return CommandRisk.MEDIUM, "Shell commands may affect local state; review before execution."


__all__ = ["CommandRisk", "classify_command_risk"]
