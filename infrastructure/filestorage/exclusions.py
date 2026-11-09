"""Paths a user chooses to keep off the network.

Sync is otherwise all-or-nothing: every session and every memory file goes. A
scratch conversation, a machine-specific note, or a directory of large captures
may not be worth mirroring, so the settings accept glob patterns that shrink
what moves.

**Subtractive only.** A pattern can remove a file from the sync; nothing here
can add one. That is why negation (``!pattern``) is rejected rather than
ignored: the deny-list in :mod:`infrastructure.filestorage.syncable` is a security
boundary, and the only way to guarantee no pattern can reach past it is for the
pattern language to have no way of saying "include". The two checks stay
independent — the deny-list runs first and unconditionally, whatever is
configured here.

Patterns are matched case-sensitively against the object key (``sessions/…``,
``memory/…``), not the absolute path, so the same settings mean the same thing
on every machine regardless of where ``~/.opensre`` lives or how the local
filesystem treats case.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

from infrastructure.filestorage.errors import RemoteSyncConfigError

#: Separates patterns written as one string (an environment variable, or a
#: single scalar in the settings file). A YAML list needs no separator.
PATTERN_SEPARATOR = ","


def _normalize(pattern: str) -> str:
    """One pattern in the form matching expects, or ``""`` to drop it.

    Windows separators and a leading ``./`` or ``/`` are accepted because that
    is how a person copies a path out of a file manager; keys use POSIX
    separators and never start with one.
    """
    cleaned = pattern.strip().replace("\\", "/")
    if cleaned.startswith("!"):
        raise RemoteSyncConfigError(
            f"exclude pattern {pattern.strip()!r} starts with '!'. Exclusions can only "
            "remove files from the sync, never add them back, so negation is not "
            "supported — remove the '!' or drop the pattern."
        )
    # Trimmed one prefix at a time, not with lstrip("./"): lstrip takes a set of
    # characters and would eat the leading dot of a pattern like ".DS_Store".
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/").rstrip("/")


def _ancestors(key: str) -> list[str]:
    """Directory paths containing ``key``, nearest first.

    Lets ``sessions/archive`` exclude everything beneath it without the user
    having to write ``sessions/archive/*`` and remember the nested case.
    """
    out: list[str] = []
    cursor = key
    while True:
        cursor = cursor.rpartition("/")[0]
        if not cursor:
            return out
        out.append(cursor)


@dataclass(frozen=True)
class ExclusionRules:
    """Glob patterns naming what stays on this machine.

    Empty by default, which is the state every existing installation is in and
    the one the engine must behave identically in.
    """

    patterns: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.patterns)

    def excludes(self, key: str) -> bool:
        """Whether the object key is held back.

        Called once per local file on push and once per stored object on pull,
        so the common case — no patterns at all — returns before allocating
        anything.
        """
        if not self.patterns:
            return False
        candidates = [key, key.rpartition("/")[2], *_ancestors(key)]
        return any(
            # fnmatchcase, not fnmatch: fnmatch folds case on Windows and macOS,
            # which would make one settings file mean two different things.
            fnmatchcase(candidate, pattern)
            for pattern in self.patterns
            for candidate in candidates
        )

    def matching(self, keys: Iterable[str]) -> int:
        """How many of ``keys`` are held back — for status output."""
        if not self.patterns:
            return 0
        return sum(1 for key in keys if self.excludes(key))


NO_EXCLUSIONS = ExclusionRules()


def parse_exclusions(raw: object) -> ExclusionRules:
    """Build rules from a settings value, whatever shape it arrived in.

    A YAML list, a single scalar, or a separated string all work, because the
    same setting is reachable through ``config.yml`` and an environment
    variable and the two should not disagree. Anything else is a mistake worth
    reporting rather than silently syncing more than the user asked for.

    Every value here is a pattern. Turning exclusions off for a run is a
    separate switch (``OPENSRE_REMOTE_SYNC_EXCLUDE_OFF``) rather than a reserved
    word, so no filename is unusable as a pattern.
    """
    if raw is None or raw == "":
        return NO_EXCLUSIONS
    if isinstance(raw, str):
        items: Sequence[object] = raw.split(PATTERN_SEPARATOR)
    elif isinstance(raw, Sequence) and not isinstance(raw, bytes | bytearray):
        items = raw
    else:
        raise RemoteSyncConfigError(
            f"remote-sync exclusions must be a list or a "
            f"{PATTERN_SEPARATOR!r}-separated string, not {type(raw).__name__}"
        )

    seen: dict[str, None] = {}
    for item in items:
        if not isinstance(item, str):
            raise RemoteSyncConfigError(
                f"remote-sync exclusion patterns must be text, not {type(item).__name__}"
            )
        normalized = _normalize(item)
        if normalized:
            # dict rather than set: duplicates go, written order stays, so
            # status prints the patterns back the way they were typed.
            seen[normalized] = None
    return ExclusionRules(patterns=tuple(seen))


__all__ = [
    "NO_EXCLUSIONS",
    "PATTERN_SEPARATOR",
    "ExclusionRules",
    "parse_exclusions",
]
