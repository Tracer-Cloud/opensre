"""Parse and load ``KEY=value`` / ``export KEY=value`` assignment lines in ``.env`` files.

A leaf so the writer (:mod:`config.env_file`) and every reader
(:mod:`config.local_env`, :mod:`config.grafana_cloud`) share one parser without
an import cycle — ``env_file`` already imports ``local_env.get_project_env_path``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Optional ``export `` prefix is how many shell-sourced env files spell assignments.
# The prefix must be a whole word (``export KEY=``), not a key that merely starts
# with those letters (``exportable=1``).
_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def env_assignment_key(line: str) -> str | None:
    """Return the env key a ``.env`` line assigns, or ``None`` for non-assignments."""
    match = _ENV_ASSIGNMENT.match(line)
    return match.group(1) if match else None


def load_env_file(path: Path, *, override: bool = False) -> None:
    """Export each assignment in ``path`` to ``os.environ``; a missing file is a no-op.

    Blank lines and ``#`` / ``;`` comments are skipped and surrounding quotes are
    stripped. A key already in the environment is kept unless ``override``.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        key = env_assignment_key(line)
        if not key:
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


__all__ = ["env_assignment_key", "load_env_file"]
