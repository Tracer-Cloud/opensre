"""Which workspace files the read-only file tools may open, and how they read them.

These tools run with no approval gate. Beyond staying inside the working
directory (:mod:`tools.system.workspace_paths`), they refuse the files a
project keeps its secrets in, and the text they return passes through the
credential redaction rules, so a question about the code cannot put a token
into the conversation.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from infrastructure.safety.secret_redaction import redact_text
from tools.system.workspace_paths import (
    WorkspacePathError,
    resolve_within_workspace,
    workspace_relative,
)

#: A read holds the whole file in memory to number its lines.
MAX_READ_BYTES = 20_000_000
#: A search skips larger files: generated bundles and data dumps, not source.
MAX_SEARCH_BYTES = 2_000_000
_BINARY_PROBE_BYTES = 8192

#: File names that hold credentials rather than code.
_SECRET_NAME_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".htpasswd",
    "*.tfstate",
    "*.tfstate.backup",
    "*.tfvars",
)
#: Suffixes of the committed stand-ins for those files, which hold no values.
_PLACEHOLDER_SUFFIXES = (".example", ".sample", ".template", ".dist", ".pub")
#: Directories whose contents are credentials or version-control internals.
SECRET_DIRECTORIES: frozenset[str] = frozenset(
    {".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker"}
)


class FileAccessError(ValueError):
    """The file cannot be read by these tools; the message says why."""


def holds_secrets(relative_path: str) -> bool:
    """Whether a workspace-relative path names a credential file or sits in a secret directory.

    Names are compared case-folded: a disk that ignores case opens ``.ENV`` as
    ``.env``.
    """
    parts = [part.casefold() for part in Path(relative_path).parts]
    if any(part in SECRET_DIRECTORIES for part in parts):
        return True
    name = parts[-1] if parts else ""
    if name.endswith(_PLACEHOLDER_SUFFIXES):
        return False
    return any(fnmatchcase(name, pattern) for pattern in _SECRET_NAME_GLOBS)


def resolve_readable(path: str) -> Path:
    """``path`` resolved inside the working directory, refused when it holds secrets."""
    try:
        resolved = resolve_within_workspace(path)
    except WorkspacePathError as exc:
        raise FileAccessError(str(exc)) from exc
    if holds_secrets(workspace_relative(resolved)):
        raise FileAccessError(
            f"{path} holds credentials and is off limits to this tool. "
            "Ask the user before reaching it another way."
        )
    return resolved


def resolve_writable(path: str) -> Path:
    """``path`` resolved inside the working directory, refused where an edit must not land.

    The same root and the same credential files as a read, so a patch cannot
    plant a git hook or rewrite a key it could not have read. The file need not
    exist yet.
    """
    resolved = resolve_readable(path)
    if resolved == resolve_readable("."):
        raise FileAccessError(f"{path} is the working directory, not a file.")
    return resolved


def read_text(path: Path, *, max_bytes: int = MAX_READ_BYTES) -> str:
    """The file as text with credentials redacted; a binary or oversized file is refused."""
    return redact_text(decode_text(path, max_bytes=max_bytes))


def decode_text(path: Path, *, max_bytes: int) -> str:
    """The file as text, unredacted: for matching only, never for output.

    Redacting every file a search opens costs more than the search; a caller
    redacts the lines it returns instead.
    """
    shown = workspace_relative(path)
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise FileAccessError(
                f"{shown} is {size:,} bytes, past the {max_bytes:,} byte limit. "
                "Read part of it with shell_run (head, tail or sed -n)."
            )
        data = path.read_bytes()
    except OSError as exc:
        raise FileAccessError(f"cannot read {shown}: {type(exc).__name__}") from exc
    if b"\0" in data[:_BINARY_PROBE_BYTES]:
        raise FileAccessError(f"{shown} is a binary file.")
    return data.decode("utf-8", errors="replace")


__all__ = [
    "MAX_READ_BYTES",
    "MAX_SEARCH_BYTES",
    "SECRET_DIRECTORIES",
    "FileAccessError",
    "decode_text",
    "holds_secrets",
    "read_text",
    "resolve_readable",
    "resolve_writable",
]
