"""Limits for reading a repository's AGENTS.md into the action prompt."""

from __future__ import annotations

from typing import Final

#: The instruction file a repository directory may carry.
REPOSITORY_INSTRUCTIONS_FILENAME: Final = "AGENTS.md"
#: A directory's local override; when present it replaces that directory's AGENTS.md.
REPOSITORY_INSTRUCTIONS_OVERRIDE_FILENAME: Final = "AGENTS.override.md"
#: One budget for every instruction file in a prompt, as Codex's ``project_doc_max_bytes``.
REPOSITORY_INSTRUCTIONS_MAX_BYTES: Final = 32 * 1024
#: Bytes read from one file: the budget plus room for redaction to see a
#: credential that crosses the cut, so a cut never leaves half a secret behind.
REPOSITORY_INSTRUCTIONS_READ_BYTES: Final = REPOSITORY_INSTRUCTIONS_MAX_BYTES + 8 * 1024
#: Longest prompt assembly waits for a remote read.
REPOSITORY_INSTRUCTIONS_FETCH_TIMEOUT_SECONDS: Final = 3.0
#: How long a remote read, including a confirmed absence, is reused.
REPOSITORY_INSTRUCTIONS_CACHE_TTL_SECONDS: Final = 600.0
#: How long a failed remote read is reused, so an outage does not stall every turn.
REPOSITORY_INSTRUCTIONS_RETRY_SECONDS: Final = 60.0
#: How long a checkout's verified ``origin`` is reused before git is asked again.
REPOSITORY_INSTRUCTIONS_CHECKOUT_TTL_SECONDS: Final = 60.0

__all__ = [
    "REPOSITORY_INSTRUCTIONS_CACHE_TTL_SECONDS",
    "REPOSITORY_INSTRUCTIONS_CHECKOUT_TTL_SECONDS",
    "REPOSITORY_INSTRUCTIONS_FETCH_TIMEOUT_SECONDS",
    "REPOSITORY_INSTRUCTIONS_FILENAME",
    "REPOSITORY_INSTRUCTIONS_MAX_BYTES",
    "REPOSITORY_INSTRUCTIONS_OVERRIDE_FILENAME",
    "REPOSITORY_INSTRUCTIONS_READ_BYTES",
    "REPOSITORY_INSTRUCTIONS_RETRY_SECONDS",
]
