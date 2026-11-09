"""REPL configuration — three-tier resolution: file → env var → CLI flag."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from config.constants.repl_theme import DEFAULT_THEME_NAME, Theme
from config.interactive_override import interactive_env_value

_FALSE_VALUES = ("", "0", "false", "off", "no")

log = logging.getLogger(__name__)


class Layout(StrEnum):
    """Interactive-shell layout: scrolling transcript vs. fixed input bar."""

    CLASSIC = "classic"
    PINNED = "pinned"


def _resolve_theme(raw: str, *, source: str | None) -> Theme:
    """Return a valid theme, defaulting on unknown input.

    ``source`` is the setting label used in the warning for env/file values; a
    ``None`` source (CLI flag) defaults silently.
    """
    try:
        return Theme((raw or "").strip().lower())
    except ValueError:
        if source is not None:
            log.warning(
                "%s=%r is not a valid theme; defaulting to %r.",
                source,
                raw,
                DEFAULT_THEME_NAME.value,
            )
        return DEFAULT_THEME_NAME


def _resolve_layout(raw: str) -> Layout:
    """Return a valid layout, defaulting to ``CLASSIC`` on unknown input."""
    try:
        return Layout((raw or "").strip().lower())
    except ValueError:
        return Layout.CLASSIC


# ── Release notes ─────────────────────────────────────────────────────────────
# Shown in the "What's new" panel on startup. Update this each release with
# exactly 2 user-visible changes. Keep each entry under ~50 chars so it fits
# the right column without truncation. The banner reads this at import time.

WHATS_NEW: tuple[str, ...] = ("New /save command exports session transcripts",)


def _read_config_file() -> dict[str, Any]:
    """Read the interactive section from ~/.opensre/config.yml.

    Returns an empty dict if the file is missing, unreadable, or malformed.
    Failures are always silent — a bad config file must never crash the CLI.
    """
    try:
        import yaml  # type: ignore[import-untyped]

        from config.constants import OPENSRE_HOME_DIR

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
    except Exception:
        return {}


@dataclass(frozen=True)
class ReplConfig:
    """REPL configuration.

    Axes
    ----
    enabled : bool
        When False the REPL is skipped and ``opensre`` falls back to
        ``render_landing()``.  Controlled by ``--no-interactive`` CLI flag,
        the ``OPENSRE_INTERACTIVE`` env var, or ``interactive.enabled`` in
        ``~/.opensre/config.yml``.

    layout : Layout  ("classic" | "pinned")
        Which renderer to use.  Only ``classic`` is wired today; ``pinned``
        is accepted and stored so the flag round-trips cleanly once P3 lands.
        Controlled by ``--layout`` CLI option, ``OPENSRE_LAYOUT`` env var, or
        ``interactive.layout`` in ``~/.opensre/config.yml``.

    theme : Theme
        Interactive shell color palette. Controlled by ``--theme`` CLI option,
        ``OPENSRE_THEME`` env var, or ``interactive.theme`` in
        ``~/.opensre/config.yml``.
    """

    enabled: bool = True
    layout: Layout = Layout.CLASSIC
    theme: Theme = DEFAULT_THEME_NAME
    alert_listener_enabled: bool = False
    alert_listener_host: str = "127.0.0.1"
    alert_listener_port: int = 0
    alert_listener_token: str | None = None

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() not in _FALSE_VALUES

    @classmethod
    def load(
        cls,
        *,
        cli_enabled: bool | None = None,
        cli_layout: str | None = None,
        cli_theme: str | None = None,
    ) -> ReplConfig:
        """Resolve config from all three tiers.

        Priority (highest wins):
            1. CLI flag   — ``cli_enabled`` / ``cli_layout`` / ``cli_theme`` params
            2. Env var    — ``OPENSRE_INTERACTIVE`` / ``OPENSRE_LAYOUT`` / ``OPENSRE_THEME``
            3. Config file — ``~/.opensre/config.yml`` ``interactive`` section
            4. Built-in defaults (enabled=True, layout="classic", theme="blue")

        A non-TTY slash command's context-local override
        (:func:`config.interactive_override.forced_non_interactive`) stands in
        for ``OPENSRE_INTERACTIVE=0`` at the env tier.

        Resolution is pure: the returned ``theme`` is a validated palette name,
        but activating it (the live terminal side effect) is the caller's job.
        """
        file_conf = _read_config_file()

        # --- enabled ---
        if cli_enabled is not None:
            enabled = cli_enabled
        elif (env_val := interactive_env_value()) is not None:
            enabled = cls._coerce_bool(env_val, default=True)
        else:
            enabled = cls._coerce_bool(file_conf.get("enabled"), default=True)

        # --- layout ---
        if cli_layout is not None:
            layout = _resolve_layout(cli_layout)
        elif (env_val := os.getenv("OPENSRE_LAYOUT")) is not None:
            layout = _resolve_layout(env_val)
        else:
            layout = _resolve_layout(str(file_conf.get("layout", Layout.CLASSIC)))

        # --- theme ---
        if cli_theme is not None:
            theme = _resolve_theme(cli_theme, source=None)
        elif (env_val := os.getenv("OPENSRE_THEME")) is not None:
            theme = _resolve_theme(env_val, source="OPENSRE_THEME")
        else:
            theme = _resolve_theme(
                str(file_conf.get("theme", DEFAULT_THEME_NAME)),
                source="config.yml interactive.theme",
            )

        # --- alert_listener_enabled ---
        if (env_val := os.getenv("OPENSRE_ALERT_LISTENER_ENABLED")) is not None:
            alert_listener_enabled = cls._coerce_bool(env_val, default=False)
        else:
            alert_listener_enabled = cls._coerce_bool(
                file_conf.get("alert_listener_enabled"), default=False
            )

        # --- alert_listener_host ---
        if (env_val := os.getenv("OPENSRE_ALERT_LISTENER_HOST")) is not None:
            alert_listener_host = env_val.strip()
        else:
            alert_listener_host = str(file_conf.get("alert_listener_host", "127.0.0.1"))

        # --- alert_listener_port ---
        if (env_val := os.getenv("OPENSRE_ALERT_LISTENER_PORT")) is not None:
            try:
                alert_listener_port = int(env_val.strip())
                if not (0 <= alert_listener_port <= 65535):
                    raise ValueError(f"Port out of range: {alert_listener_port}")
            except (ValueError, TypeError):
                log.warning(
                    "OPENSRE_ALERT_LISTENER_PORT=%r is not a valid port number; defaulting to 0 (random).",
                    env_val,
                )
                alert_listener_port = 0
        else:
            raw_port = file_conf.get("alert_listener_port", 0)
            try:
                if isinstance(raw_port, (bool, float)) or raw_port is None:
                    raise TypeError(f"Invalid port type: {type(raw_port).__name__}")
                alert_listener_port = int(raw_port)
                if not (0 <= alert_listener_port <= 65535):
                    raise ValueError(f"Port out of range: {alert_listener_port}")
            except (ValueError, TypeError):
                log.warning(
                    "config.yml interactive.alert_listener_port=%r is not a valid port number; defaulting to 0 (random).",
                    raw_port,
                )
                alert_listener_port = 0

        # --- alert_listener_token ---
        if (env_val := os.getenv("OPENSRE_ALERT_LISTENER_TOKEN")) is not None:
            alert_listener_token = env_val.strip() or None
        else:
            alert_listener_token = file_conf.get("alert_listener_token") or None

        return cls(
            enabled=enabled,
            layout=layout,
            theme=theme,
            alert_listener_enabled=alert_listener_enabled,
            alert_listener_host=alert_listener_host,
            alert_listener_port=alert_listener_port,
            alert_listener_token=alert_listener_token,
        )

    @classmethod
    def from_env(cls) -> ReplConfig:
        """Convenience alias — loads from env + file, no CLI override."""
        return cls.load()


def read_history_settings() -> dict[str, Any]:
    """Return the ``interactive.history`` config block, or empty dict."""
    interactive = _read_config_file()
    raw = interactive.get("history", {})
    return raw if isinstance(raw, dict) else {}


def read_prompt_log_settings() -> dict[str, Any]:
    """Return the ``interactive.prompt_log`` config block, or empty dict."""
    interactive = _read_config_file()
    raw = interactive.get("prompt_log", {})
    return raw if isinstance(raw, dict) else {}
