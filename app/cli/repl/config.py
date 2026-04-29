"""REPL configuration — three-tier resolution: file → env var → CLI flag."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

_VALID_LAYOUTS = ("classic", "pinned")


def _read_config_file() -> dict[str, Any]:
    """Read the interactive section from ~/.opensre/config.yml.

    Returns an empty dict if the file is missing, unreadable, or malformed.
    Failures are always silent — a bad config file must never crash the CLI.
    """
    try:
        import yaml  # type: ignore[import-untyped]

        from app.constants import OPENSRE_HOME_DIR

        config_path = OPENSRE_HOME_DIR / "config.yml"
        if not config_path.exists():
            return {}

        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}

        interactive = data.get("interactive", {})
        if not isinstance(interactive, dict):
            return {}

        return interactive
    except Exception:  # noqa: BLE001
        return {}


@dataclass(frozen=True)
class ReplConfig:
    """Two-axis REPL configuration.

    Axes
    ----
    enabled : bool
        When False the REPL is skipped and ``opensre`` falls back to
        ``render_landing()``.  Controlled by ``--no-interactive`` CLI flag,
        the ``OPENSRE_INTERACTIVE`` env var, or ``interactive.enabled`` in
        ``~/.opensre/config.yml``.

    layout : str  ("classic" | "pinned")
        Which renderer to use.  Only ``classic`` is wired today; ``pinned``
        is accepted and stored so the flag round-trips cleanly once P3 lands.
        Controlled by ``--layout`` CLI option, ``OPENSRE_LAYOUT`` env var, or
        ``interactive.layout`` in ``~/.opensre/config.yml``.
    """

    enabled: bool = True
    layout: str = "classic"

    @classmethod
    def load(
        cls,
        *,
        cli_enabled: bool | None = None,
        cli_layout: str | None = None,
    ) -> ReplConfig:
        """Resolve config from all three tiers.

        Priority (highest wins):
            1. CLI flag   — ``cli_enabled`` / ``cli_layout`` params
            2. Env var    — ``OPENSRE_INTERACTIVE`` / ``OPENSRE_LAYOUT``
            3. Config file — ``~/.opensre/config.yml`` ``interactive`` section
            4. Built-in defaults (enabled=True, layout="classic")
        """
        file_conf = _read_config_file()

        # --- enabled ---
        if cli_enabled is not None:
            enabled = cli_enabled
        elif (env_val := os.getenv("OPENSRE_INTERACTIVE")) is not None:
            enabled = env_val.lower() not in ("0", "false", "off")
        else:
            raw = file_conf.get("enabled", True)
            enabled = bool(raw)

        # --- layout ---
        if cli_layout is not None:
            layout = cli_layout.lower()
        elif (env_val := os.getenv("OPENSRE_LAYOUT")) is not None:
            layout = env_val.lower()
        else:
            layout = str(file_conf.get("layout", "classic")).lower()

        if layout not in _VALID_LAYOUTS:
            layout = "classic"

        return cls(enabled=enabled, layout=layout)

    @classmethod
    def from_env(cls) -> ReplConfig:
        """Convenience alias — loads from env + file, no CLI override."""
        return cls.load()
