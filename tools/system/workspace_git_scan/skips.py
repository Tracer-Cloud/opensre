"""Home folders a workspace scan leaves out, and the note that names them."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# Media and app folders: large trees that do not hold git checkouts.
_ALWAYS_SKIPPED = ("Applications", "Movies", "Music", "Pictures", "Public")
# On macOS, listing these opens a privacy permission dialog that blocks the scan.
MACOS_PRIVACY_PROTECTED = ("Desktop", "Documents", "Downloads")
_MACOS = "darwin"


def default_skip_paths(home: Path, *, cwd: Path | None, platform: str) -> frozenset[Path]:
    """Folders under *home* the scan does not enter.

    Media and app folders on every platform; on macOS also Desktop, Documents and
    Downloads, whose listing opens a privacy dialog that blocks the scan. A folder
    holding *cwd* is kept: the user works there, so the terminal already has access.
    """
    names = _ALWAYS_SKIPPED + (MACOS_PRIVACY_PROTECTED if platform == _MACOS else ())
    folders = (home / name for name in names)
    return frozenset(folder for folder in folders if cwd is None or not cwd.is_relative_to(folder))


def skipped_note(skipped: Iterable[str]) -> str:
    """One line naming the skipped privacy-protected folders; empty when none was skipped."""
    names = [Path(path).name for path in skipped]
    protected = [name for name in names if name in MACOS_PRIVACY_PROTECTED]
    if not protected:
        return ""
    return f"Skipped {', '.join(protected)} (macOS privacy-protected); name one to scan it."


__all__ = ["MACOS_PRIVACY_PROTECTED", "default_skip_paths", "skipped_note"]
