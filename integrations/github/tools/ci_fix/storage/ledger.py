"""Atomic storage of distinct GitHub CI repair identities."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from config.constants.ci_fixes import CI_FIX_LEDGER_LOCK_TIMEOUT_SECONDS


class CorruptLedgerError(ValueError):
    """The ledger cannot be decoded as the current schema."""


class UnsupportedLedgerVersion(ValueError):
    """The ledger belongs to a different version and must remain untouched."""


def read_fix_ids(path: Path) -> set[str]:
    """Read repair identities; a missing ledger is empty."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CorruptLedgerError("Invalid CI fix ledger JSON") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        raise CorruptLedgerError("Invalid CI fix ledger schema")
    if data["version"] != 1:
        raise UnsupportedLedgerVersion("Unsupported CI fix ledger version")
    fixes = data.get("fixes")
    if not isinstance(fixes, list) or any(
        not isinstance(key, str)
        or len(key) != 64
        or any(char not in "0123456789abcdef" for char in key)
        for key in fixes
    ):
        raise CorruptLedgerError("Invalid CI fix identities")
    return set(fixes)


def append_fix_ids(path: Path, identities: set[str]) -> set[str]:
    """Merge identities under a bounded process lock, preserving corrupt input."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=CI_FIX_LEDGER_LOCK_TIMEOUT_SECONDS):
        try:
            stored = read_fix_ids(path)
        except (CorruptLedgerError, UnicodeError):
            quarantine = path.with_name(f"{path.name}.corrupt-{uuid4().hex}")
            os.replace(path, quarantine)
            stored = set()
        merged = stored | identities
        if merged != stored or not path.exists():
            _write_fix_ids(path, merged)
        return merged


def _write_fix_ids(path: Path, identities: set[str]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump({"version": 1, "fixes": sorted(identities)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                temporary.unlink()
