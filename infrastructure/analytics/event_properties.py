"""Property builders for analytics events."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Final

from config.constants.analytics import (
    ANALYTICS_INSTALL_CHANNEL_ENV,
    ANALYTICS_INSTALL_SOURCE_ENV,
    ANALYTICS_INSTALL_VERSION_ENV,
)
from infrastructure.analytics.provider import Properties
from infrastructure.analytics.repl_context import get_cli_session_id
from infrastructure.safety.secret_redaction import redact_text

_INSTALL_DIMENSION_MAX_CHARS: Final[int] = 80


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _mapping_value(mapping: Mapping[str, object], key: str) -> str | None:
    return _string_value(mapping.get(key))


def _onboard_completed_properties(config: Mapping[str, object]) -> Properties:
    properties: Properties = {}

    wizard_obj = config.get("wizard")
    if isinstance(wizard_obj, Mapping):
        wizard_mode = _mapping_value(wizard_obj, "mode")
        configured_target = _mapping_value(wizard_obj, "configured_target")
        if wizard_mode is not None:
            properties["wizard_mode"] = wizard_mode
        if configured_target is not None:
            properties["configured_target"] = configured_target

    targets_obj = config.get("targets")
    if isinstance(targets_obj, Mapping):
        local_obj = targets_obj.get("local")
        if isinstance(local_obj, Mapping):
            provider = _mapping_value(local_obj, "provider")
            model = _mapping_value(local_obj, "model")
            if provider is not None:
                properties["provider"] = provider
            if model is not None:
                properties["model"] = model

    return properties


def _integration_lifecycle_properties(service: str) -> Properties:
    properties: Properties = {"service": service}
    session_id = get_cli_session_id()
    if session_id:
        properties["cli_session_id"] = session_id
    return properties


def _bucket_duration_ms(duration_ms: float) -> str:
    if duration_ms < 500:
        return "<500ms"
    if duration_ms < 1000:
        return "500ms-1s"
    if duration_ms < 3000:
        return "1s-3s"
    if duration_ms < 5000:
        return "3s-5s"
    return ">=5s"


def _bucket_percentage(percent: float) -> str:
    if percent < 25:
        return "0-24"
    if percent < 50:
        return "25-49"
    if percent < 75:
        return "50-74"
    if percent < 95:
        return "75-94"
    return "95-100"


def _bounded_redacted_text(value: object, *, max_chars: int) -> str:
    text = redact_text(str(value)).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1].rstrip()}…"


def _optional_install_dimension(raw: str) -> str | None:
    text = _bounded_redacted_text(raw, max_chars=_INSTALL_DIMENSION_MAX_CHARS)
    return text or None


def build_install_detected_properties(*, entrypoint: str) -> Properties:
    """Build install dimensions supplied by an installer or inferred on first run."""
    source = _optional_install_dimension(os.getenv(ANALYTICS_INSTALL_SOURCE_ENV, ""))
    properties: Properties = {
        "entrypoint": entrypoint,
        "install_source": source or "first_cli_invocation",
        "distribution": (
            "frozen_binary"
            if bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))
            else "python_package"
        ),
    }
    if channel := _optional_install_dimension(os.getenv(ANALYTICS_INSTALL_CHANNEL_ENV, "")):
        properties["install_channel"] = channel
    if version := _optional_install_dimension(os.getenv(ANALYTICS_INSTALL_VERSION_ENV, "")):
        properties["installed_version"] = version
    return properties


def _terminal_properties() -> Properties:
    properties: Properties = {}
    for name, stream in (("stdin_is_tty", sys.stdin), ("stdout_is_tty", sys.stdout)):
        try:
            properties[name] = bool(stream.isatty())
        except (AttributeError, OSError, ValueError):
            continue
    return properties


def build_cli_invoked_properties(
    *,
    entrypoint: str,
    command_parts: list[str],
    json_output: bool = False,
    verbose: bool = False,
    debug: bool = False,
    yes: bool = False,
    interactive: bool | None = None,
    interactive_option_source: str = "caller",
) -> Properties:
    """Build a structured ``cli_invoked`` payload for any CLI surface.

    Used by ``opensre`` (Click-driven) and the ``python -m app.*`` entrypoints
    so all three end up with the same property names. Records command names
    only — never raw argv values, option values, paths, URLs, or secrets.
    """
    properties: Properties = {
        "entrypoint": entrypoint,
        "command_path": " ".join((entrypoint, *command_parts)),
        "command_family": command_parts[0] if command_parts else "root",
        "json_output": json_output,
        "verbose": verbose,
        "debug": debug,
        "yes": yes,
        **_terminal_properties(),
    }
    if interactive is not None:
        properties["interactive_option"] = interactive
        properties["interactive_option_source"] = interactive_option_source
    if len(command_parts) > 1:
        properties["subcommand"] = command_parts[1]
    if command_parts:
        properties["command_leaf"] = command_parts[-1]
    return properties
