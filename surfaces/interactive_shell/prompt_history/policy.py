"""Privacy controls for the interactive-shell command history.

Built-in redaction patterns + a ``RedactingFileHistory`` that subclasses
``prompt_toolkit.FileHistory`` to apply redaction before each entry is
persisted. Settings resolve from env vars and the ``interactive.history``
section of ``~/.opensre/config.yml``; built-in defaults keep
redaction on, persistence on, and entries capped at 5000.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Any, ClassVar

from filelock import BaseFileLock, FileLock
from prompt_toolkit.history import FileHistory
from pydantic import ConfigDict

from config.secret_redaction import DEFAULT_REDACTION_RULES, RedactionRule
from config.strict_config import StrictConfigModel
from infrastructure.safety.secret_redaction import redact_text

DEFAULT_MAX_ENTRIES = 5000


class HistoryPolicy(StrictConfigModel):
    """Resolved privacy policy for the prompt-history persistence layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    redact: bool = True
    max_entries: int = DEFAULT_MAX_ENTRIES

    _ENV_ENABLED: ClassVar[str] = "OPENSRE_HISTORY_ENABLED"
    _ENV_REDACT: ClassVar[str] = "OPENSRE_HISTORY_REDACT"
    _ENV_MAX_ENTRIES: ClassVar[str] = "OPENSRE_HISTORY_MAX_ENTRIES"

    @classmethod
    def load(cls, file_settings: dict[str, Any] | None = None) -> HistoryPolicy:
        """Resolve from env -> file -> defaults (env wins)."""
        file_settings = file_settings or {}
        return cls(
            enabled=_parse_bool(os.getenv(cls._ENV_ENABLED), file_settings.get("enabled"), True),
            redact=_parse_bool(os.getenv(cls._ENV_REDACT), file_settings.get("redact"), True),
            max_entries=_parse_int(
                os.getenv(cls._ENV_MAX_ENTRIES),
                file_settings.get("max_entries"),
                DEFAULT_MAX_ENTRIES,
            ),
        )


def _parse_bool(env_val: str | None, file_val: Any, default: bool) -> bool:
    if env_val is not None and env_val != "":
        return env_val.strip().lower() not in {"0", "false", "off", "no"}
    if isinstance(file_val, bool):
        return file_val
    if isinstance(file_val, str) and file_val.strip() != "":
        return file_val.strip().lower() not in {"0", "false", "off", "no"}
    if file_val is not None:
        return bool(file_val)
    return default


def _parse_int(env_val: str | None, file_val: Any, default: int) -> int:
    if env_val is not None and env_val.strip():
        try:
            return max(0, int(env_val.strip()))
        except ValueError:
            return default
    if isinstance(file_val, int) and file_val >= 0:
        return file_val
    return default


def history_file_lock(
    filename: str | bytes | os.PathLike[str] | os.PathLike[bytes],
) -> BaseFileLock:
    """Return the cross-process lock for a prompt-history file."""
    return FileLock(f"{os.fsdecode(filename)}.lock")


class RefreshingFileHistory(FileHistory):
    """File history that reloads when another shell changes the backing file."""

    def __init__(self, filename: str) -> None:
        super().__init__(filename)
        self._loaded_signature: tuple[int, int, int] | None = None

    def _file_signature(self) -> tuple[int, int, int] | None:
        try:
            stat = os.stat(self.filename)
        except OSError:
            return None
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _load_history_strings_unlocked(self) -> list[str]:
        return list(super().load_history_strings())

    def _store_string_unlocked(self, string: str) -> None:
        super().store_string(string)
        self._loaded_signature = self._file_signature()

    async def load(self) -> AsyncGenerator[str]:
        """Yield current entries, reloading after a peer modifies the file."""
        with history_file_lock(self.filename):
            signature = self._file_signature()
            if not self._loaded or signature != self._loaded_signature:
                self._loaded_strings = self._load_history_strings_unlocked()
                self._loaded = True
                self._loaded_signature = self._file_signature()
            entries = tuple(self._loaded_strings)

        for item in entries:
            yield item

    def load_history_strings(self) -> Iterator[str]:
        """Yield a consistent snapshot of entries while writers are excluded."""
        with history_file_lock(self.filename):
            entries = self._load_history_strings_unlocked()
        yield from entries

    def store_string(self, string: str) -> None:
        """Append one entry while excluding concurrent readers and writers."""
        with history_file_lock(self.filename):
            self._store_string_unlocked(string)


class RedactingFileHistory(RefreshingFileHistory):
    """``FileHistory`` that redacts known token shapes before persisting.

    Also enforces a max-entry retention cap by rewriting the file when
    the new total exceeds the cap. ``paused`` lets ``/history off`` stop
    persistence at runtime without swapping the History instance.
    """

    def __init__(
        self,
        filename: str,
        *,
        rules: tuple[RedactionRule, ...] = DEFAULT_REDACTION_RULES,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        super().__init__(filename)
        self.paused: bool = False
        self._rules = rules
        self._max_entries = max_entries
        self._entry_count: int | None = None

    def load_history_strings(self) -> Iterator[str]:
        """Yield entries with CRLF artifacts from the on-disk format normalized away."""
        for item in super().load_history_strings():
            yield item.rstrip("\r\n")

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def set_max_entries(self, max_entries: int, *, prune: bool = True) -> None:
        self._max_entries = max(0, max_entries)
        if prune:
            self._prune_to_cap()

    def store_string(self, string: str) -> None:
        if self.paused:
            return
        cleaned = redact_text(string, self._rules) if self._rules else string
        with history_file_lock(self.filename):
            self._store_string_unlocked(cleaned)
            if self._max_entries <= 0:
                return
            if self._entry_count is None:
                self._entry_count = self._count_entries_unlocked()
            else:
                self._entry_count += 1
            if self._entry_count > self._max_entries:
                self._prune_to_cap_unlocked()

    def _prune_to_cap(self) -> None:
        with history_file_lock(self.filename):
            self._prune_to_cap_unlocked()

    def _prune_to_cap_unlocked(self) -> None:
        if self._max_entries <= 0:
            return
        path = Path(os.fsdecode(self.filename))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        lines = text.splitlines(keepends=True)
        # Each persisted entry begins with a "# <timestamp>" comment line.
        boundaries = [i for i, ln in enumerate(lines) if ln.startswith("# ")]
        if len(boundaries) <= self._max_entries:
            self._entry_count = len(boundaries)
            return

        keep_from = boundaries[len(boundaries) - self._max_entries]
        if keep_from > 0 and lines[keep_from - 1].strip() == "":
            keep_from -= 1
        try:
            path.write_text("".join(lines[keep_from:]), encoding="utf-8")
            self._entry_count = self._max_entries
            self._loaded_signature = self._file_signature()
        except OSError:
            return

    def _count_entries_unlocked(self) -> int:
        path = Path(os.fsdecode(self.filename))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        return sum(1 for line in text.splitlines() if line.startswith("# "))


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_REDACTION_RULES",
    "HistoryPolicy",
    "RefreshingFileHistory",
    "RedactingFileHistory",
    "RedactionRule",
    "history_file_lock",
    "redact_text",
]
