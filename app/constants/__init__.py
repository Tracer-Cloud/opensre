"""Application-wide constants: prompts, limits, identifiers, and filesystem paths."""

from __future__ import annotations

from pathlib import Path

TRACER_HOME_DIR: Path = Path.home() / ".tracer"
INTEGRATIONS_STORE_PATH: Path = TRACER_HOME_DIR / "integrations.json"

OPENSRE_HOME_DIR: Path = Path.home() / ".opensre"
