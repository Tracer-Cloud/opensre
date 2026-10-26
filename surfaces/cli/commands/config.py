"""CLI commands for LLM/env config and local ~/.opensre/config.yml."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import click

from config.local_settings import (
    LocalSettingsError,
    load_local_settings,
    local_settings_path,
    save_local_settings,
    set_nested_key,
)
from infrastructure.terminal.theme import GLYPH_SUCCESS

_SUPPORTED_LAYOUTS = {"classic", "pinned"}


def _supported_themes() -> set[str]:
    from infrastructure.terminal.theme import list_theme_names

    return set(list_theme_names())


def _masked(value: str | None) -> str:
    if not value:
        return "(not set)"
    return value[:4] + "****" if len(value) > 4 else "****"


def _emit_llm_config() -> None:
    """Print current LLM provider and model from environment (legacy `opensre config`)."""
    from config.llm_auth.credentials import status as credential_status
    from config.llm_settings import get_configured_llm_provider, get_llm_provider_api_key_env
    from infrastructure.process.runtime_flags import is_json_output

    provider = get_configured_llm_provider()
    auth_status = credential_status(provider)

    model_env_by_provider: dict[str, str] = {
        "anthropic": "ANTHROPIC_REASONING_MODEL",
        "openai": "OPENAI_REASONING_MODEL",
        "openrouter": "OPENROUTER_REASONING_MODEL",
        "trustedrouter": "TRUSTEDROUTER_REASONING_MODEL",
        "deepseek": "DEEPSEEK_REASONING_MODEL",
        "gemini": "GEMINI_REASONING_MODEL",
        "nvidia": "NVIDIA_REASONING_MODEL",
        "bedrock": "BEDROCK_MODEL",
        "minimax": "MINIMAX_REASONING_MODEL",
        "ollama": "OLLAMA_MODEL",
    }

    key_env = get_llm_provider_api_key_env(provider) or {
        "bedrock": "AWS_DEFAULT_REGION",
        "ollama": "OLLAMA_HOST",
    }.get(provider, "")
    key_value = os.getenv(key_env, "") if key_env and auth_status.source == "env" else ""
    model_env = model_env_by_provider.get(provider, "")
    model_value = os.getenv(model_env, "") if model_env else ""

    if is_json_output():
        click.echo(
            json.dumps(
                {
                    "provider": provider,
                    "model": model_value or None,
                    "api_key_set": auth_status.configured and not auth_status.stale,
                    "auth_source": auth_status.source,
                    "auth_stale": auth_status.stale,
                }
            )
        )
        return

    click.echo(f"Provider : {provider}")
    if model_value:
        click.echo(f"Model    : {model_value}")
    if key_env:
        masked = _masked(key_value) if key_value else f"({auth_status.source})"
        click.echo(f"{key_env:<16}: {masked}")
    click.echo(f"Auth     : {auth_status.detail}")
    click.echo()
    click.echo("To log in or change LLM auth, run: opensre auth login")
    click.echo("To rerun LLM setup, run: opensre onboard")
    click.echo("Local CLI YAML: opensre config show / opensre config set …")


def _load_config() -> dict[str, Any]:
    try:
        return load_local_settings()
    except LocalSettingsError as exc:
        raise click.ClickException(f"Could not parse local config file: {exc}") from exc


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise click.UsageError(
        "Invalid value for interactive.enabled. Use one of: true/false, 1/0, yes/no, on/off."
    )


def _coerce_enabled(raw_value: str) -> bool:
    return _parse_bool(raw_value)


def _coerce_layout(raw_value: str) -> str:
    layout = raw_value.strip().lower()
    if layout not in _SUPPORTED_LAYOUTS:
        raise click.UsageError("Invalid value for interactive.layout. Use 'classic' or 'pinned'.")
    return layout


def _coerce_theme(raw_value: str) -> str:
    theme = raw_value.strip().lower()
    supported_themes = _supported_themes()
    if theme not in supported_themes:
        supported = ", ".join(sorted(supported_themes))
        raise click.UsageError(f"Invalid value for interactive.theme. Use one of: {supported}.")
    return theme


_COERCERS: dict[str, Callable[[str], bool | str]] = {
    "interactive.enabled": _coerce_enabled,
    "interactive.layout": _coerce_layout,
    "interactive.theme": _coerce_theme,
}
_SUPPORTED_KEYS = tuple(_COERCERS)


def _coerce_value(key: str, raw_value: str) -> bool | str:
    coercer = _COERCERS.get(key)
    if coercer is None:
        raise click.UsageError(
            f"Unknown config key '{key}'. Supported keys: {', '.join(_SUPPORTED_KEYS)}"
        )
    return coercer(raw_value)


@click.group(name="config", invoke_without_command=True)
@click.pass_context
def config_command(ctx: click.Context) -> None:
    """LLM/environment config by default; subcommands manage ~/.opensre/config.yml."""
    if ctx.invoked_subcommand is None:
        _emit_llm_config()


@config_command.command(name="show")
def config_show() -> None:
    """Show local ~/.opensre/config.yml values."""
    from infrastructure.process.runtime_flags import is_json_output

    payload = _load_config()

    if is_json_output():
        click.echo(json.dumps(payload))
        return

    import yaml

    path = local_settings_path()
    click.echo(f"# {path} (on-disk values; environment variables do not override this output)")
    click.echo(yaml.safe_dump(payload, sort_keys=False).rstrip())


@config_command.command(name="set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set one local config key in ~/.opensre/config.yml."""
    key = key.strip()
    coerced = _coerce_value(key, value)
    data = _load_config()
    updated = set_nested_key(data, key, coerced)
    save_local_settings(updated)
    click.echo(f"{GLYPH_SUCCESS} Set {key} = {coerced}")
