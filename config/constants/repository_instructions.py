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

# The prompt block's layout. The renderer and the analytics omitter
# (``infrastructure.safety.repository_instructions_redaction``) share these, so
# exports can always find the text they must leave out.
#: Starts the header line of a section that carries file text.
REPOSITORY_INSTRUCTIONS_HEADER_PREFIX: Final = "REPOSITORY INSTRUCTIONS (AGENTS.md for "
#: Starts each file's wrapper inside a section (Codex's wording).
REPOSITORY_INSTRUCTIONS_FILE_PREFIX: Final = "# AGENTS.md instructions for "
REPOSITORY_INSTRUCTIONS_OPEN_TAG: Final = "<INSTRUCTIONS>"
#: Closes each file's wrapper; a file's own text never contains it.
REPOSITORY_INSTRUCTIONS_CLOSE_TAG: Final = "</INSTRUCTIONS>"

__all__ = [
    "REPOSITORY_INSTRUCTIONS_CACHE_TTL_SECONDS",
    "REPOSITORY_INSTRUCTIONS_CHECKOUT_TTL_SECONDS",
    "REPOSITORY_INSTRUCTIONS_CLOSE_TAG",
    "REPOSITORY_INSTRUCTIONS_FETCH_TIMEOUT_SECONDS",
    "REPOSITORY_INSTRUCTIONS_FILENAME",
    "REPOSITORY_INSTRUCTIONS_FILE_PREFIX",
    "REPOSITORY_INSTRUCTIONS_HEADER_PREFIX",
    "REPOSITORY_INSTRUCTIONS_MAX_BYTES",
    "REPOSITORY_INSTRUCTIONS_OPEN_TAG",
    "REPOSITORY_INSTRUCTIONS_OVERRIDE_FILENAME",
    "REPOSITORY_INSTRUCTIONS_READ_BYTES",
    "REPOSITORY_INSTRUCTIONS_RETRY_SECONDS",
]
