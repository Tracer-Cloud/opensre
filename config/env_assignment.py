"""Parse ``KEY=value`` / ``export KEY=value`` assignment lines in ``.env`` files.

A leaf so both the writer (:mod:`config.env_file`) and the reader
(:mod:`config.local_env`) share one parser without an import cycle —
``env_file`` already imports ``local_env.get_project_env_path``.
"""

from __future__ import annotations

import re

# Optional ``export `` prefix is how many shell-sourced env files spell assignments.
# The prefix must be a whole word (``export KEY=``), not a key that merely starts
# with those letters (``exportable=1``).
_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def env_assignment_key(line: str) -> str | None:
    """Return the env key a ``.env`` line assigns, or ``None`` for non-assignments."""
    match = _ENV_ASSIGNMENT.match(line)
    return match.group(1) if match else None


__all__ = ["env_assignment_key"]
